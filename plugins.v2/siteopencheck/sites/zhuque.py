from typing import Dict, Any, Tuple

from app.log import logger
from app.plugins.siteopencheck.sites import _ISiteOpenCheckHandler


class ZhuqueOpenCheckHandler(_ISiteOpenCheckHandler):
    """默认的开注检查处理器，内置默认页面获取与检测逻辑。"""

    site_url = "https://zhuque.in/"

    def build_signup_url(self, site_info: Dict[str, Any]) -> str:
        site_url = site_info.get("url", "")
        return f"{site_url.rstrip('/')}/entry/regist"

    def check(self, site_info: Dict[str, Any]) -> Tuple[str, str]:
        # 构建注册URL
        signup_url = self.build_signup_url(site_info)

        # 获取页面
        page_source, final_url = self.get_page_source(signup_url)

        closed_keywords = ["未开放自由注册"]
        for keyword in closed_keywords:
            if keyword in page_source:
                return "closed", f"检测到关闭注册关键词: {keyword}"

        return "open", "未检测到关闭注册关键词，可能开放注册"
