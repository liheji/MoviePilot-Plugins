from typing import Tuple

from lxml import etree
from ruamel.yaml import CommentedMap

from app.core.config import settings
from app.log import logger
from app.plugins.autosignin.sites import _ISiteSigninHandler
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


class Tjupt(_ISiteSigninHandler):
    """
    北洋签到
    """
    # 匹配的站点Url，每一个实现类都需要设置为自己的站点Url
    site_url = "tjupt.org"

    # 签到地址
    _sign_in_url = 'https://www.tjupt.org/attendance.php'

    # 已签到
    _sign_regex = ['<a href="attendance.php">今日已签到</a>']

    # 签到成功
    _succeed_regex = ['[今日已签到]']

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
        openai = site_info.get("openai")

        if not openai:
            logger.error("ChatGPT插件未配置")
            return False, '签到失败，ChatGPT插件未配置'

        # 获取北洋签到页面html
        html_text = self.get_page_source(url=self._sign_in_url,
                                         cookie=site_cookie,
                                         ua=ua,
                                         proxy=proxy,
                                         render=render)

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
        img_url = html.xpath('//table[@class="captcha"]//img/@src')[0]

        if not img_url:
            logger.error(f"{site} 签到失败，未获取到签到图片")
            return False, '签到失败，未获取到签到图片'

        # 签到图片
        img_url = "https://www.tjupt.org" + img_url
        logger.info(f"获取到签到图片 {img_url}")

        # 签到答案选项
        values = html.xpath("//input[@name='ban_robot']/@value")
        options = html.xpath("//input[@name='ban_robot']/following-sibling::text()")

        if not values or not options:
            logger.error(f"{site} 签到失败，未获取到答案选项")
            return False, '签到失败，未获取到答案选项'

        # value+选项
        answers = list(zip(values, options))
        logger.info(f"获取到所有签到选项 {options}")

        ret, result = openai.get_answer_with_img("\n".join(options), img_url)
        if not ret:
            logger.error("ChatGPT请求失败，未返回答案")
            return False, '签到失败，ChatGPT未返回答案'

        logger.info(f"ChatGPT返回答案 {result}")
        for value, answer in answers:
            if str(result).lower().strip() == str(answer).lower().strip():
                # 匹配成功
                return self.__signin(
                    answer=value,
                    site_cookie=site_cookie,
                    ua=ua,
                    proxy=proxy,
                    site=site
                )

        # 没有匹配签到成功，则签到失败
        return False, '签到失败，未获取到匹配答案'

    def __signin(self, answer, site_cookie, ua, proxy, site):
        """
        签到请求
        """
        data = {
            'ban_robot': answer,
            'submit': '提交'
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
