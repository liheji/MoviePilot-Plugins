from typing import Dict, Any, Tuple

import re
import requests
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
        session = requests.Session()
        session.headers.update({
            "Host": "zhuque.in",
            "User-Agent": self._ua,
        })
        page_res = session.get('https://zhuque.in/entry/regist')
        if page_res.status_code != 200:
            raise RuntimeError(f"无法访问注册页，状态码: {page_res.status_code}")

        match = re.search(r'name="x-csrf-token" content="(.+?)"', page_res.text)
        if not match:
            raise RuntimeError("未能在页面中找到 csrf-token")
        csrf_token = match.group(1)
        session.headers.update({
            "x-csrf-token": csrf_token,
        })
        res = session.get(signup_url)
        if res is None:
            raise RuntimeError("无法访问页面，响应为空")
        if res.status_code != 200:
            raise RuntimeError(f"无法访问页面，状态码: {res.status_code}")

        json_data = res.json()
        if json_data['data']['registOpen']:
            return "open", f"检测到开放注册关键词(registOpen=true)，可能开放注册"

        return "closed", "未检测到开放注册关键词"
