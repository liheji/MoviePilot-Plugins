from typing import Dict, Any, Tuple

from app.utils.http import RequestUtils
from app.plugins.siteopencheck.sites import _ISiteOpenCheckHandler


class TjuptOpenCheckHandler(_ISiteOpenCheckHandler):
    """默认的开注检查处理器，内置默认页面获取与检测逻辑。"""

    site_url = "https://www.tjupt.org"

    def build_signup_url(self, site_info: Dict[str, Any]) -> str:
        site_url = site_info.get("url", "")
        return f"{site_url.rstrip('/')}/api_signup.php"

    def check(self, site_info: Dict[str, Any]) -> Tuple[str, str]:
        # 构建注册URL
        signup_url = self.build_signup_url(site_info)

        # 获取页面
        res = RequestUtils(ua=self._ua, timeout=self._timeout).get_res(url=signup_url)
        if res is None:
            raise RuntimeError("无法访问页面，响应为空")
        if res.status_code != 200:
            raise RuntimeError(f"无法访问页面，状态码: {res.status_code}")

        json_data = res.json()

        # 关键词匹配
        if "不开放自由注册" in json_data['msg']:
            return "closed", "检测到关闭注册关键词: 不开放自由注册"

        return "open", "未检测到关闭注册数据，可能开放注册"
