from abc import ABCMeta, abstractmethod
import time
from typing import Tuple, Dict, Any

from app.utils.http import RequestUtils


class _ISiteOpenCheckHandler(metaclass=ABCMeta):
    """
    站点开注检查适配器接口。
    - match: 判断是否匹配该站点
    - init: 初始化通用配置参数
    - build_signup_url: 返回站点注册页URL，默认 {base_url}/signup.php
    - check: 返回 (status, message)
    """

    site_url = ""
    _timeout = 15
    _retry_interval = 5
    _ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

    @classmethod
    def init(cls, timeout: int = 15, retry_interval: int = 5):
        """初始化通用配置参数"""
        cls._timeout = timeout
        cls._retry_interval = retry_interval

    @classmethod
    def match(cls, url: str) -> bool:
        from app.utils.string import StringUtils
        if not cls.site_url:
            return False
        return StringUtils.url_equal(url, cls.site_url)

    def get_page_source(self, url: str) -> Tuple[str, str]:
        """默认页面获取逻辑：先请求一次，失败重试一次。"""
        last_error = None
        for attempt in range(2):
            try:
                res = RequestUtils(ua=self._ua, timeout=self._timeout).get_res(url=url)
                if res is None:
                    raise RuntimeError("无法访问页面，响应为空")
                if res.status_code != 200:
                    raise RuntimeError(f"无法访问页面，状态码: {res.status_code}")
                return res.text, str(res.url)
            except Exception as e:
                last_error = str(e)
                time.sleep(self._retry_interval)
        raise RuntimeError(last_error or "未知错误")

    @abstractmethod
    def build_signup_url(self, site_info: Dict[str, Any]) -> str:
        """构建注册页面URL"""
        site_url = site_info.get("url", "")
        return f"{site_url.rstrip('/')}/signup.php"

    @abstractmethod
    def check(self, site_info: Dict[str, Any]) -> Tuple[str, str]:
        """ 返回状态和信息 """
        return "unknown", "未实现"
