# -*- coding: utf-8 -*-
import base64
import re
import time
from abc import ABCMeta, abstractmethod
from typing import Tuple

import chardet
from lxml import etree
from ruamel.yaml import CommentedMap

from app.core.config import settings
from app.helper.browser import PlaywrightHelper
from app.helper.ocr import OcrHelper
from app.log import logger
from app.plugins.autosigninnew.result import visible_html
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


class _ISiteSigninHandler(metaclass=ABCMeta):
    """
    实现站点签到的基类，所有站点签到类都需要继承此类，并实现match和signin方法
    实现类放置到sitesignin目录下将会自动加载
    """
    # 匹配的站点Url，每一个实现类都需要设置为自己的站点Url
    site_url = ""
    # 签到入口若已经执行登录保号，失败后不能再调用相同接口作为登录回退。
    signin_is_login: bool = False

    @abstractmethod
    def match(self, url: str) -> bool:
        """
        根据站点Url判断是否匹配当前站点签到类，大部分情况使用默认实现即可
        :param url: 站点Url
        :return: 是否匹配，如匹配则会调用该类的signin方法
        """
        if StringUtils.url_equal(url, self.site_url):
            return True
        return False

    @abstractmethod
    def signin(self, site_info: CommentedMap) -> Tuple[bool, str]:
        """
        执行签到操作
        :param site_info: 站点信息，含有站点Url、站点Cookie、UA等信息
        :return: True|False,签到结果信息
        """
        pass

    @staticmethod
    def get_page_source(url: str, cookie: str, ua: str, proxy: bool, render: bool,
                        token: str = None, timeout: int = None) -> str:
        """
        获取页面源码
        :param url: Url地址
        :param cookie: Cookie
        :param ua: UA
        :param proxy: 是否使用代理
        :param render: 是否渲染
        :param token: JWT Token
        :param timeout: 请求超时时间，单位秒
        :return: 页面源码，错误信息
        """
        if render:
            return PlaywrightHelper().get_page_source(url=url,
                                                      cookies=cookie,
                                                      ua=ua,
                                                      proxies=settings.PROXY_SERVER if proxy else None,
                                                      timeout=timeout or 60)
        else:
            if token:
                headers = {
                    "Authorization": token,
                    "User-Agent": ua
                }
            else:
                headers = {
                    "User-Agent": ua,
                    "Cookie": cookie
                }
            res = RequestUtils(headers=headers,
                               proxies=settings.PROXY if proxy else None,
                               timeout=timeout or 20).get_res(url=url)
            if res is not None and res.status_code == 200:
                # 使用chardet检测字符编码
                raw_data = res.content
                if raw_data:
                    try:
                        result = chardet.detect(raw_data)
                        encoding = result['encoding']
                        # 解码为字符串
                        return raw_data.decode(encoding)
                    except Exception as e:
                        logger.error(f"chardet解码失败：{str(e)}")
                        return res.text
                else:
                    return res.text
            return ""

    @staticmethod
    def sign_in_result(html_res: str, regexs: list) -> bool:
        """
        仅匹配可见页面中的签到结果，脚本和隐藏模板里的文案不代表已执行。
        """
        html_text = re.sub(r"#\d+", "", re.sub(r"\d+px", "", visible_html(html_res)))
        document = etree.HTML(html_text)
        text = "".join(document.itertext()) if document is not None else ""
        if re.search(r"(?:签到|簽到)(?:失败|失敗|未成功)|(?:未|没有|沒有)(?:签到|簽到)成功", text):
            return False
        for regex in regexs:
            # 纯文案只查正文，不能命中 onclick/title 等属性里的成功提示。
            target = html_text if "<" in regex or "value=" in regex else text
            if re.search(str(regex), target):
                return True
        return False

    @staticmethod
    def download_image(img_url, site_cookie, ua, proxy, site):
        """
        下载图片并转换为 base64 data URI，供大模型识图使用。
        """
        img_res = RequestUtils(cookies=site_cookie,
                               ua=ua,
                               proxies=proxy).get_res(url=img_url)
        if not img_res or img_res.status_code != 200:
            logger.error(f"{site} 获取图片 {img_url} 请求失败")
            return False, ''

        # 获取 MIME 类型
        content_type = img_res.headers.get('Content-Type', '')
        mime_type = _ISiteSigninHandler.__detect_mime_type(content_type, img_res.content)

        if not mime_type:
            return False, ''

        # Base64 编码
        img_base64 = base64.b64encode(img_res.content).decode('utf-8')
        return True, f"data:{mime_type};base64,{img_base64}"

    @staticmethod
    def captcha_ocr(img_url, site, site_cookie=None, ua=None, length: int = 6):
        """
        ocr识别多次，获取 length 位验证码
        """
        times = 0
        ocr_result = None

        while times <= 5:  # 循环识别5次
            try:
                # 进行 ocr 识别
                ocr_result = OcrHelper().get_captcha_text(image_url=img_url,
                                                          cookie=site_cookie,
                                                          ua=ua)
                logger.debug(f"OCR识别 {site} 验证码 {ocr_result}")
                if ocr_result and len(ocr_result) == length:
                    logger.info(f"OCR识别 {site}验证码成功 {ocr_result}")
                    break
            except Exception as e:
                logger.error(f"OCR识别 {site} 验证码失败：{str(e)}")
            times += 1
            logger.debug(f"OCR识别 {site} 验证码失败，正在进行重试，目前重试次数 {times}")
            time.sleep(1)

        return ocr_result

    @staticmethod
    def __detect_mime_type(content_type: str, data: bytes) -> str:
        """
        根据 Content-Type 头和文件签名判断图片 MIME 类型。
        """
        # 常见图片签名（magic number）
        image_signatures = {
            b'\xFF\xD8\xFF': 'image/jpeg',
            b'\x89PNG\r\n\x1a\n': 'image/png',
            b'GIF87a': 'image/gif',
            b'GIF89a': 'image/gif',
            b'RIFF': 'image/webp',  # WebP 以 RIFF 开头
            b'BM': 'image/bmp',
        }

        # 优先按文件签名判断
        for signature, mime in image_signatures.items():
            if data.startswith(signature):
                return mime

        # 回退到 Content-Type 头
        if content_type:
            mime = content_type.split(';')[0].strip().lower()
            if mime.startswith('image/'):
                return mime

        return ''
