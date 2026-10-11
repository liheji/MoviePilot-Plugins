from typing import Tuple

from lxml import etree
from ruamel.yaml import CommentedMap

from app.core.config import settings
from app.log import logger
from app.plugins.autosigninnew.sites import _ISiteSigninHandler
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


class PtSoulvoice(_ISiteSigninHandler):
    """
    聆音Club签到
    """
    # 匹配的站点Url，每一个实现类都需要设置为自己的站点Url
    site_url = "pt.soulvoice.club"

    # 签到地址
    _sign_in_url = 'https://pt.soulvoice.club/attendance.php'

    # 已签到
    _sign_regex = ['签到已得']

    # 签到成功
    _succeed_regex = ['签到已得']

    @classmethod
    def match(cls, url: str) -> bool:
        """
        根据站点Url判断是否匹配当前站点签到类，大部分情况使用默认实现即可
        :param url: 站点Url
        :return: 是否匹配，如匹配则会调用该类的signin方法
        """
        return True if StringUtils.url_equal(url, cls.site_url) else False

    def signin(self, site_info: CommentedMap) -> Tuple[bool, str]:
        """
        执行签到操作
        :param site_info: 站点信息，含有站点Url、站点Cookie、UA等信息
        :return: 签到结果信息
        """
        site = site_info.get("name")
        site_cookie = site_info.get("cookie")
        ua = site_info.get("ua")
        proxy = site_info.get("proxy")
        render = site_info.get("render")
        timeout = site_info.get("timeout")

        # 获取聆音Club签到页面html
        html_text = self.get_page_source(url=self._sign_in_url,
                                         cookie=site_cookie,
                                         ua=ua,
                                         proxy=proxy,
                                         render=render,
                                         timeout=timeout)

        # 获取签到后返回html，判断是否签到成功
        if not html_text:
            logger.error(f"{site} 签到失败，请检查站点连通性")
            return False, '签到失败，请检查站点连通性'

        if "login.php" in html_text:
            logger.error(f"{site} 签到失败，Cookie已失效")
            return False, '签到失败，Cookie已失效'

        sign_status = self.sign_in_result(html_res=html_text,
                                          regexs=self._sign_regex)
        if sign_status:
            logger.info(f"{site} 今日已签到")
            return True, '今日已签到'

        # 没有签到则解析html
        html = etree.HTML(html_text)
        if not html:
            return False, '签到失败'

        img_url = html.xpath('//img[@alt="CAPTCHA"]/@src')[0]
        img_hash = html.xpath('//input[@name="imagehash"]/@value')[0]
        if not img_url or not img_hash:
            logger.error(f"{site} 签到失败，获取签到参数失败")
            return False, '签到失败，获取签到参数失败'

        # 签到图片
        img_url = "https://pt.soulvoice.club/" + img_url
        logger.info(f"获取到验证码链接 {img_url}")

        # ocr识别多获取6位验证码
        ocr_result = self.captcha_ocr(img_url, site, site_cookie, ua, 6)

        if ocr_result:
            logger.info(f"OCR识别结果为 {ocr_result}")
            # 匹配成功
            return self.__signin(
                hash=img_hash,
                answer=ocr_result,
                site_cookie=site_cookie,
                ua=ua,
                proxy=proxy,
                site=site
            )

        # 没有匹配签到成功，则签到失败
        return False, '签到失败，OCR验证码失败'

    def __signin(self, hash, answer, site_cookie, ua, proxy, site):
        """
        签到请求
        """
        data = {
            'imagestring': answer,
            'imagehash': hash
        }
        logger.debug(f"提交data {data}")
        sign_in_res = RequestUtils(cookies=site_cookie,
                                   ua=ua,
                                   proxies=settings.PROXY if proxy else None
                                   ).post_res(url=self._sign_in_url, data=data)
        if not sign_in_res or sign_in_res.status_code != 200:
            logger.error(f"{site} 签到失败，签到接口请求失败")
            return False, '签到失败，签到接口请求失败'

        # 获取签到后返回html，判断是否签到成功
        sign_status = self.sign_in_result(html_res=sign_in_res.text,
                                          regexs=self._succeed_regex)
        if sign_status:
            logger.info(f"{site} 签到成功")
            return True, '签到成功'
        else:
            logger.error(f"{site} 签到失败，请到页面查看")
            return False, '签到失败，请到页面查看'
