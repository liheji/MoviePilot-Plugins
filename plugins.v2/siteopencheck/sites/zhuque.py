from typing import Dict, Any, Tuple

from app.log import logger
from app.plugins.siteopencheck.sites import _ISiteOpenCheckHandler


class ZhuqueOpenCheckHandler(_ISiteOpenCheckHandler):
    """默认的开注检查处理器，内置默认页面获取与检测逻辑。"""

    site_url = "https://zhuque.in/"

    def build_signup_url(self, site_info: Dict[str, Any]) -> str:
        site_url = site_info.get("url", "")
        return f"{site_url.rstrip('/')}/api/user/registStatus"

    def check(self, site_info: Dict[str, Any]) -> Tuple[str, str]:
        # 构建注册URL
        signup_url = self.build_signup_url(site_info)

        # 获取页面
        page_source, final_url = self.get_page_source(signup_url)

        if '"registOpen":true' in page_source:
            return "open", f"检测到开放注册关键词registOpen:true，可能开放注册"

        return "closed", "未检测到开放注册关键词"
