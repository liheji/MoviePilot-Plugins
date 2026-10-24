from urllib.parse import urlparse

from ..parser import BONUS_PATH, TASK_PATH, parse_hourly_magic, parse_tasks


class NexusTaskSite:
    """NexusPHP task adapter; also the final fallback for unknown sites."""

    site_url = ""
    task_path = TASK_PATH
    bonus_path = BONUS_PATH
    user_path = "/userdetails.php"

    @classmethod
    def match(cls, url: str) -> bool:
        if not cls.site_url:
            return False
        host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
        return host == cls.site_url or host.endswith(f".{cls.site_url}")

    @staticmethod
    def parse_tasks(html: str, now, hourly_magic: float = 0,
                    user_level: str = "", join_days=None):
        return parse_tasks(
            html, now, hourly_magic, user_level=user_level, join_days=join_days
        )

    @staticmethod
    def parse_hourly_magic(html: str):
        return parse_hourly_magic(html)
