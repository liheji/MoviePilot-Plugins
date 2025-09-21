# -*- coding: utf-8 -*-
from abc import ABCMeta, abstractmethod
from typing import List, Dict
from ruamel.yaml import CommentedMap

from app.core.config import settings
from app.log import logger
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


class _IMedalSiteHandler(metaclass=ABCMeta):
    """
    实现站点勋章获取的基类，所有站点勋章类都需要继承此类，并实现match和fetch_medals方法
    实现类放置到sites目录下将会自动加载
    """
    # 匹配的站点Url，每一个实现类都需要设置为自己的站点Url
    site_url = ""

    @abstractmethod
    def match(self, url: str) -> bool:
        """
        根据站点Url判断是否匹配当前站点勋章类，大部分情况使用默认实现即可
        :param url: 站点Url
        :return: 是否匹配，如匹配则会调用该类的fetch_medals方法
        """
        if StringUtils.url_equal(url, self.site_url):
            return True
        return False

    @abstractmethod
    def fetch_medals(self, site_info: CommentedMap) -> List[Dict]:
        """
        执行勋章获取操作
        :param site_info: 站点信息，含有站点Url、站点Cookie、UA等信息
        :return: 勋章数据列表
        """
        pass

    @staticmethod
    def get_page_source(url: str, cookie: str, ua: str, proxy: bool, render: bool,
                        timeout: int = None) -> str:
        """
        获取页面源码
        :param url: Url地址
        :param cookie: Cookie
        :param ua: UA
        :param proxy: 是否使用代理
        :param render: 是否渲染
        :param timeout: 请求超时时间，单位秒
        :return: 页面源码
        """
        if render:
            from app.helper.browser import PlaywrightHelper
            return PlaywrightHelper().get_page_source(url=url,
                                                      cookies=cookie,
                                                      ua=ua,
                                                      proxies=settings.PROXY_SERVER if proxy else None,
                                                      timeout=timeout or 60)
        else:
            headers = {
                "User-Agent": ua,
                "Cookie": cookie
            }
            res = RequestUtils(headers=headers,
                               proxies=settings.PROXY if proxy else None,
                               timeout=timeout or 20).get_res(url=url)
            if res is not None:
                return res.text
            return ""

    @staticmethod
    def format_medal_data(medal: Dict) -> Dict:
        """统一格式化勋章数据"""
        return {
            'name': medal.get('name', ''),  # 勋章名称
            'description': medal.get('description', ''),  # 勋章描述
            'imageSmall': medal.get('imageSmall', ''),  # 勋章图片
            'saleBeginTime': medal.get('saleBeginTime', ''),  # 销售开始时间
            'saleEndTime': medal.get('saleEndTime', ''),  # 销售结束时间
            'price': medal.get('price', 0),  # 勋章价格
            'site': medal.get('site', ''),  # 所属站点
            'validity': medal.get('validity', ''),  # 有效期
            'bonus_rate': medal.get('bonus_rate', ''),  # 加成比例
            'purchase_status': medal.get('purchase_status', ''),  # 购买状态
            'gift_status': medal.get('gift_status', ''),  # 赠送状态
            'stock': medal.get('stock', ''),  # 库存数量
        }
