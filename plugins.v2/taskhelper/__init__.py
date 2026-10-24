from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.db.site_oper import SiteOper
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.utils.http import RequestUtils

from .parser import (
    BONUS_PATH,
    TASK_PATH,
    apply_site_lock,
    evaluate_task,
    format_amount,
    parse_join_date,
    parse_site_user_level,
    user_profile_href,
    userdetails_links,
)
from .sites.base import NexusTaskSite
from .sites import TASK_SITE_HANDLERS


class TaskHelper(_PluginBase):
    plugin_name = "PT任务助手"
    plugin_desc = (
        "按站点定时只读抓取任务，完整展示指标、时间、目标用户、认领人数、奖惩与描述。"
        "任务按做种积分、发种、上传&下载分类，站点适配优先、NexusPHP Base 兜底；"
        "插件不提交领取请求。"
    )
    plugin_icon = "PTHelper_A.png"
    plugin_version = "1.1.7"
    plugin_author = "yilee"
    author_url = "https://github.com/yilee"
    plugin_config_prefix = "taskhelper_"
    plugin_order = 20
    auth_level = 2

    _scheduler: Optional[BackgroundScheduler] = None
    _site_schema: List[type] = list(TASK_SITE_HANDLERS)
    _enabled: bool = False
    _onlyonce: bool = False
    _cron: str = "0 */6 * * *"
    _site_ids: List[Any] = []
    _timeout: int = 20

    def init_plugin(self, config: dict = None):
        self.stop_service()
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._onlyonce = bool(config.get("onlyonce"))
        self._cron = config.get("cron") or "0 */6 * * *"
        self._site_ids = config.get("site_ids") or []
        self._timeout = int(config.get("timeout") or 20)

        if (self._enabled or self._onlyonce) and not self._site_ids:
            logger.warning("[任务助手] 未选择站点，插件已关闭")
            self._enabled = False
            self._onlyonce = False
        self.__update_config()

        if self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.refresh,
                trigger="date",
                run_date=self._now() + timedelta(seconds=3),
                name="PT任务助手",
            )
            self._scheduler.start()
            self._onlyonce = False
            self.__update_config()

    def get_state(self) -> bool:
        return self._enabled

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown(wait=False)
                self._scheduler = None
        except Exception as err:
            logger.error(f"[任务助手] 停止服务失败：{err}")

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            try:
                return [{
                    "id": "TaskHelper",
                    "name": "PT任务助手-定时抓取",
                    "trigger": CronTrigger.from_crontab(self._cron),
                    "func": self.refresh,
                    "kwargs": {},
                }]
            except ValueError as err:
                logger.error(f"[任务助手] Cron 配置无效：{err}")
        return []

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "site_ids": self._site_ids,
            "timeout": self._timeout,
        })

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=pytz.timezone(settings.TZ))

    @staticmethod
    def _base_url(site: Dict) -> str:
        parsed = urlparse(site.get("url") or "")
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return (site.get("url") or "").rstrip("/")

    @staticmethod
    def _domain(site: Dict) -> str:
        return site.get("domain") or urlparse(site.get("url") or "").hostname or ""

    @staticmethod
    def _dump_site(site: Dict) -> Dict:
        """站点全量信息，cookie 只保留长度避免日志泄露。"""
        info = dict(site)
        cookie = info.get("cookie")
        if cookie:
            info["cookie"] = f"<已隐藏，{len(cookie)}字符>"
        return info

    def _get_page(self, site: Dict, path: str) -> Optional[str]:
        base = self._base_url(site)
        url = path if path.startswith("http") else urljoin(base + "/", path)
        proxies = settings.PROXY if site.get("proxy") else None
        try:
            response = RequestUtils(
                cookies=site.get("cookie"),
                ua=site.get("ua"),
                proxies=proxies,
                timeout=self._timeout,
            ).get_res(url=url)
        except Exception as err:
            logger.error(f"[任务助手] {site.get('name')} 请求失败：{err}")
            return None
        if response is None or response.status_code != 200:
            logger.warning(
                f"[任务助手] {site.get('name')} 请求 {path} 失败："
                f"HTTP {response.status_code if response else '无响应'}"
            )
            return None
        return response.text

    def _handler_for(self, url: str):
        for handler in self._site_schema:
            if handler.match(url):
                return handler
        return NexusTaskSite

    @staticmethod
    def _user_metadata() -> Dict[str, Dict]:
        try:
            return {
                item.domain: {
                    "level": item.user_level or "",
                    "join_at": item.join_at or "",
                }
                for item in SiteOper().get_userdata_latest()
                if item.domain
            }
        except Exception:
            return {}

    @staticmethod
    def _join_days(join_at: str, now: datetime) -> Optional[int]:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d, %H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                joined = datetime.strptime(join_at.strip(), fmt)
                return (now.date() - joined.date()).days
            except (AttributeError, ValueError):
                continue
        return None

    def refresh(self):
        sites = {str(site.get("id")): site for site in SitesHelper().get_indexers()}
        users = self._user_metadata()
        now = self._now().replace(tzinfo=None)
        summary = {"sites": 0, "tasks": 0, "actionable": 0, "errors": 0}

        for site_id in self._site_ids:
            site = sites.get(str(site_id))
            if not site:
                continue
            key = f"site_{site_id}"
            previous = self.get_data(key) or {}
            handler = self._handler_for(site.get("url") or "")
            # shortcut: selected sites fit one task page; add pagination when a site exceeds it.
            html = self._get_page(site, handler.task_path)
            if html is None:
                previous.update({
                    "site_id": site_id,
                    "site_name": site.get("name") or str(site_id),
                    "error": "任务页获取失败",
                    "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
                })
                self.save_data(key, previous)
                summary["errors"] += 1
                continue

            bonus_html = self._get_page(site, handler.bonus_path) or ""
            hourly = handler.parse_hourly_magic(bonus_html)
            metadata = users.get(self._domain(site), {})
            profile_html = ""
            user_level = parse_site_user_level(html) or metadata.get("level", "")
            join_days = self._join_days(metadata.get("join_at", ""), now)
            if not user_level or join_days is None:
                # 任务页信息栏里当前用户的 userdetails 链接（含 id）才有注册日期
                profile_href = user_profile_href(html) or handler.user_path
                profile_html = self._get_page(site, profile_href) or ""
                if not user_level:
                    user_level = parse_site_user_level(profile_html)
                if join_days is None:
                    join_days = self._join_days(parse_join_date(profile_html) or "", now)
            if not user_level:
                logger.warning(
                    f"[任务助手] {site.get('name')} 未获取当前用户等级，诊断信息：\n"
                    f"站点全量信息：{self._dump_site(site)}\n"
                    f"本地用户数据（domain={self._domain(site)}）："
                    f"{metadata if metadata else '未匹配到域名'}\n"
                    f"任务页用户链接：{userdetails_links(html) or '页面无 userdetails 链接'}\n"
                    f"个人页用户链接："
                    f"{userdetails_links(profile_html) or '页面无 userdetails 链接'}"
                )
            tasks = handler.parse_tasks(
                html, now, hourly or 0, user_level=user_level, join_days=join_days
            )
            if join_days is None and any(
                task.get("registered_days_min") is not None
                or task.get("registered_days_max") is not None
                for task in tasks
            ):
                logger.warning(
                    f"[任务助手] {site.get('name')} 任务含注册天数条件但未获取到注册时间，"
                    f"个人页：{'已抓取' if profile_html else '获取失败'}，"
                    f"本地用户数据（domain={self._domain(site)}）："
                    f"{metadata if metadata else '未匹配到域名'}"
                )
            error = "" if tasks else "未解析到任务"
            if not tasks and previous.get("tasks"):
                tasks = previous["tasks"]
                error += "，已保留上次结果"
            data = {
                "site_id": site_id,
                "site_name": site.get("name") or str(site_id),
                "site_url": self._base_url(site),
                "adapter": handler.__name__,
                "hourly_magic": hourly or 0,
                "hourly_found": hourly is not None,
                "user_level": user_level,
                "join_days": join_days,
                "tasks": tasks,
                "error": error,
                "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.save_data(key, data)
            summary["sites"] += 1
            summary["tasks"] += len(tasks)
            summary["actionable"] += sum(task["state"] == "去领取" for task in tasks)
            logger.info(
                f"[任务助手] {data['site_name']}（{handler.__name__}）："
                f"{len(tasks)} 个任务，时魔 "
                f"{hourly if hourly is not None else '未获取'}"
            )

        self.save_data("last_run", summary)
        logger.info(f"[任务助手] 抓取完成：{summary}")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        version = getattr(settings, "VERSION_FLAG", "v1")
        cron_component = "VCronField" if version == "v2" else "VTextField"
        try:
            sites = [
                {"title": site.get("name"), "value": site.get("id")}
                for site in SitesHelper().get_indexers()
                if not site.get("public")
            ]
        except Exception:
            sites = []

        return [{
            "component": "VForm",
            "content": [
                {
                    "component": "VRow",
                    "content": [
                        self._field(4, "VSwitch", "enabled", "启用插件"),
                        self._field(4, "VSwitch", "onlyonce", "立即运行一次"),
                        self._field(4, "VTextField", "timeout", "请求超时(秒)",
                                    type="number", variant="outlined"),
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        self._field(12, "VSelect", "site_ids", "选择站点",
                                    items=sites, multiple=True, chips=True,
                                    variant="outlined"),
                        self._field(12, cron_component, "cron", "抓取周期",
                                    placeholder="0 */6 * * *"),
                    ],
                },
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "tonal",
                        "density": "compact",
                        "text": "只读抓取 task.php 与 mybonus.php，不提交领取请求。"
                                "站点适配器优先，未知站点使用 NexusPHP Base；"
                                "判断时间、等级、注册天数、认领人数和最低时魔后提供任务页跳转。",
                    },
                },
            ],
        }], {
            "enabled": False,
            "onlyonce": False,
            "cron": "0 */6 * * *",
            "site_ids": [],
            "timeout": 20,
        }

    @staticmethod
    def _field(cols: int, component: str, model: str, label: str, **props) -> dict:
        return {
            "component": "VCol",
            "props": {"cols": 12, "sm": cols},
            "content": [{
                "component": component,
                "props": {
                    "model": model,
                    "label": label,
                    "color": "primary",
                    "hide-details": True,
                    **props,
                },
            }],
        }

    def get_page(self) -> List[dict]:
        now = self._now().replace(tzinfo=None)
        cards = []
        totals = {"sites": 0, "tasks": 0, "claimed": 0, "actionable": 0}
        for site_id in self._site_ids:
            data = self.get_data(f"site_{site_id}")
            if not data:
                continue
            totals["sites"] += 1
            tasks = data.get("tasks") or []
            for task in tasks:
                task["_site_url"] = data.get("site_url") or ""
                task["state"] = evaluate_task(
                    task,
                    data.get("hourly_magic") or 0,
                    now,
                    data.get("user_level") or "",
                    data.get("join_days"),
                )
            apply_site_lock(tasks)
            totals["claimed"] += sum(task["state"] == "已领取" for task in tasks)
            totals["actionable"] += sum(task["state"] == "去领取" for task in tasks)
            totals["tasks"] += len(tasks)
            cards.append(self._site_card(data))

        last_run = self.get_data("last_run") or {}
        if not cards:
            return [{
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "暂无任务数据，等待定时执行或打开“立即运行一次”",
                },
            }]

        return [self._title("📋 PT任务助手"), self._stats(totals, last_run), *cards]

    def _site_card(self, data: Dict) -> dict:
        hourly = data.get("hourly_magic") or 0
        hourly_text = f"{hourly:g}/h" if data.get("hourly_found") and hourly > 0 else "未获取"
        content = [{
            "component": "div",
            "props": {"class": "d-flex flex-wrap align-center ga-2 mb-3"},
            "content": [
                {
                    "component": "div",
                    "props": {"class": "text-h6 font-weight-bold"},
                    "text": f"🌐 {data.get('site_name')}",
                },
                self._chip(f"基础时魔 {hourly_text}", "#16b1ff"),
                self._chip(f"更新 {data.get('updated', '-')}", "#64748b"),
                self._chip(
                    f"等级 {data['user_level']}" if data.get("user_level")
                    else "等级 未获取",
                    "#0ea5e9" if data.get("user_level") else "#f59e0b",
                ),
            ],
        }]
        if data.get("error"):
            content.append({
                "component": "VAlert",
                "props": {"type": "warning", "variant": "tonal", "density": "compact",
                          "text": data["error"]},
            })
        for task in data.get("tasks") or []:
            content.append(self._task_card(task))
        return {
            "component": "VCard",
            "props": {"variant": "flat", "class": "mb-4 pa-4"},
            "content": content,
        }

    def _task_card(self, task: Dict) -> dict:
        state = task.get("state") or "未知"
        colors = {
            "去领取": "#22c55e",
            "已领取": "#64748b",
            "本站已领取": "#64748b",
            "时魔不足": "#f59e0b",
            "时魔未获取": "#f59e0b",
            "等级不符": "#ef4444",
            "等级未获取": "#f59e0b",
            "注册天数不符": "#ef4444",
            "注册天数未获取": "#f59e0b",
            "认领已满": "#ef4444",
            "未开始": "#64748b",
            "已结束": "#64748b",
            "不可领取": "#64748b",
        }
        action_props = {
            "color": "primary" if state == "去领取" else colors.get(state, "#64748b"),
            "variant": "outlined" if state == "去领取" else "flat",
            "size": "small",
            "disabled": state != "去领取",
        }
        if state == "去领取":
            action_props.update({
                "href": f"{task.get('_site_url', '').rstrip('/')}{TASK_PATH}",
                "target": "_blank",
            })
        action = [{
            "component": "VBtn",
            "props": action_props,
            "text": "去领取" if state == "去领取" else state,
            "content": [{
                "component": "VTooltip",
                "props": {"activator": "parent", "location": "top"},
                "text": task.get("reason") or state,
            }],
        }]
        metrics = [
            self._chip(line, "#e0f2fe", dark=True)
            for line in task.get("metrics") or []
        ]
        time_text = " ~ ".join(v for v in (task.get("start"), task.get("end")) if v)
        conditions = [
            self._chip(f"时间：{time_text or '-'}", "#f1f5f9",
                       dark=True, text_color="#334155"),
            *[
            self._chip(
                item["value"] if not item.get("name")
                else f"{item['name']}：{item['value']}",
                "#22c55e" if item.get("ok") is True else
                "#ef4444" if item.get("ok") is False else "#f59e0b",
            )
            for item in task.get("checks") or []
            ],
            self._chip(
                f"{task.get('reward_name') or '完成奖励'}：{format_amount(task.get('reward', ''))}",
                "#dcfce7",
                dark=True,
                text_color="#15803d",
            ),
            self._chip(
                f"{task.get('penalty_name') or '失败扣除'}：{format_amount(task.get('penalty', ''))}",
                "#fee2e2",
                dark=True,
                text_color="#b91c1c",
            ),
        ]
        fields = []
        for name, value in (task.get("extra_fields") or {}).items():
            fields.append((name, format_amount(value)))
        if task.get("description"):
            fields.append(("描述", task["description"]))

        detail_cards = []
        for name, value in fields:
            detail_cards.append({
                "component": "VCol",
                "props": {"cols": 12, "sm": 12 if name == "描述" else 3},
                "content": [{
                    "component": "div",
                    "props": {
                        "class": "text-caption",
                        "style": "color:#94a3b8; white-space:pre-wrap; word-break:break-word",
                    },
                    "text": f"{name}：{value}",
                }],
            })

        return {
            "component": "VCard",
            "props": {"variant": "outlined", "class": "mb-3 pa-4"},
            "content": [
                {
                    "component": "VRow",
                    "props": {"class": "align-center mb-2"},
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 7,
                                      "class": "d-flex flex-wrap align-center ga-2"},
                            "content": [
                                {
                                    "component": "div",
                                    "props": {"class": "text-h6 font-weight-bold"},
                                    "text": task.get("name") or "-",
                                },
                                *[self._chip(category, "#0ea5e9")
                                  for category in task.get("categories") or []],
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 5,
                                      "class": "d-flex flex-wrap justify-md-end align-center ga-2"},
                            "content": action,
                        },
                    ],
                },
                {
                    "component": "div",
                    "props": {"class": "d-flex flex-wrap ga-2 mb-3"},
                    "content": metrics,
                },
                {
                    "component": "div",
                    "props": {"class": "d-flex flex-wrap ga-2 mb-3"},
                    "content": conditions,
                },
                *({"component": "VRow", "content": detail_cards} for _ in [0]
                  if detail_cards),
            ],
        }

    @staticmethod
    def _title(text: str) -> dict:
        return {
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 12},
                "content": [{
                    "component": "h3",
                    "props": {"class": "text-h5 font-weight-bold mb-4"},
                    "text": text,
                }],
            }],
        }

    @staticmethod
    def _stats(totals: Dict, last_run: Dict) -> dict:
        return {
            "component": "VRow",
            "props": {"class": "mb-4"},
            "content": [{
                "component": "VCol",
                "props": {"cols": 12},
                "content": [{
                    "component": "div",
                    "props": {"class": "d-flex flex-wrap ga-2"},
                    "content": [
                        {"component": "VChip", "props": {"variant": "flat", "color": "grey-lighten-4"},
                         "text": f"站点 {totals['sites']}"},
                        {"component": "VChip", "props": {"variant": "flat", "color": "grey-lighten-4"},
                         "text": f"任务 {totals['tasks']}"},
                        {"component": "VChip", "props": {"variant": "flat", "color": "green-lighten-4"},
                         "text": f"已领取 {totals['claimed']}"},
                        {"component": "VChip", "props": {"variant": "flat", "color": "green-lighten-4"},
                         "text": f"可去领取 {totals['actionable']}"},
                        {"component": "VChip", "props": {"variant": "flat", "color": "grey-lighten-4"},
                         "text": f"上次错误 {last_run.get('errors', 0)}"},
                    ],
                }],
            }],
        }

    @staticmethod
    def _chip(text: str, color: str, dark: bool = False,
              text_color: str = "#0f172a") -> dict:
        return {
            "component": "VChip",
            "props": {
                "size": "small",
                "variant": "flat",
                "color": color,
                "class": "" if dark else "text-white",
                "style": f"color:{text_color}" if dark else "",
            },
            "text": text,
        }
