import re
from typing import Dict, Any, Tuple

from app.log import logger
from app.plugins.siteopencheck.sites import _ISiteOpenCheckHandler


class DefaultOpenCheckHandler(_ISiteOpenCheckHandler):
    """默认的开注检查处理器，内置默认页面获取与检测逻辑。"""

    site_url = ""

    @classmethod
    def match(cls, url: str) -> bool:
        # 作为兜底处理器，不参与匹配
        return False

    def build_signup_url(self, site_info: Dict[str, Any]) -> str:
        site_url = site_info.get("url", "")
        return f"{site_url.rstrip('/')}/signup.php"

    def check(self, site_info: Dict[str, Any]) -> Tuple[str, str]:
        # 构建注册URL
        signup_url = self.build_signup_url(site_info)

        # 获取页面
        page_source, final_url = self.get_page_source(signup_url)

        if '/signup' not in final_url:
            return "unknown", f"不支持的注册模板: {final_url}"

        # 关键词匹配
        closed_keywords = [
            # 简体中文
            "自由注册当前关闭", "自由注册关闭", "对不起", "抱歉", "注册已关闭", "暂不开放注册", "注册功能暂时关闭",
            "注册暂时关闭", "注册功能已关闭", "暂时关闭注册", "注册已暂停", "注册关闭", "关闭注册", "注册暂停",
            "暂停注册", "不开放自由注册", "封闭运行",
            # 繁体
            "自由註冊當前關閉", "自由註冊關閉", "對不起", "抱歉", "註冊已關閉", "暫不開放註冊", "註冊功能暫時關閉",
            "註冊暫時關閉", "註冊功能已關閉", "暫時關閉註冊", "註冊已暫停", "註冊關閉", "關閉註冊", "註冊暫停",
            "暫停註冊", "不開放自由註冊",
            # 英文
            "No moar open signups", "Signup-ul este momentan oprit",
            "Free registration not engaged", "Registration is closed", "Registration is temporarily closed",
        ]
        for keyword in closed_keywords:
            if keyword in page_source:
                return "closed", f"检测到关闭注册关键词: {keyword}"

        # 检查是否有提交按钮或包含注册的按钮
        if 'type="submit"' in page_source:
            return "open", "检测到提交按钮，可能开放注册"

        # 按钮匹配
        open_keywords = [
            # 简体
            "注册", "立即注册", "免费注册", "新用户注册", "用户注册",
            # 繁体
            "註冊", "立即註冊", "免費註冊", "新用戶註冊", "用戶註冊",
            # 英文
            "Sign Up", "Sign up", "Create account", "Create Account"
        ]
        for keyword in open_keywords:
            if re.search(rf'<button[^>]*>.*{re.escape(keyword)}.*</button>', page_source, re.IGNORECASE):
                return "open", f"检测到注册按钮: {keyword}"

        for keyword in open_keywords:
            if re.search(rf'<input[^>]*>.*{re.escape(keyword)}.*</input>', page_source, re.IGNORECASE):
                return "open", f"检测到注册输入框: {keyword}"

        # 检查表单中的注册相关字段
        if re.search(r'<form[^>]*>.*(?:注册|註冊|register|signup).*</form>', page_source,
                     re.IGNORECASE | re.DOTALL):
            return "open", "检测到注册表单，可能开放注册"

        return "unknown", "无法确定注册状态"
