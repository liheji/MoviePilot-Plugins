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
    CLAIMABLE_STATE,
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
        "按站点定时抓取任务，完整展示指标、时间、目标用户、认领人数、奖惩与描述。"
        "任务按做种积分、发种、上传&下载分类，站点适配优先、NexusPHP Base 兜底；"
        "支持直接领取和每站唯一默认任务定时领取。"
    )
    plugin_icon = "PTHelper_A.png"
    plugin_version = "1.2.2"
    plugin_author = "liheji"
    author_url = "https://github.com/liheji"
    plugin_config_prefix = "taskhelper_"
    plugin_order = 20
    auth_level = 2

    _scheduler: Optional[BackgroundScheduler] = None
    _site_schema: List[type] = list(TASK_SITE_HANDLERS)
    _enabled: bool = False
    _onlyonce: bool = False
    _cron: str = "0 * * * *"
    _default_claim_cron: str = "*/5 * * * *"
    _default_claims: Dict[str, Dict[str, Any]] = {}
    _site_ids: List[Any] = []
    _timeout: int = 20
    TASK_AJAX_PATH = "/ajax.php"

    def init_plugin(self, config: dict = None):
        self.stop_service()
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._onlyonce = bool(config.get("onlyonce"))
        self._cron = config.get("cron") or "0 * * * *"
        self._default_claim_cron = (
            config.get("default_claim_cron") or "*/5 * * * *"
        )
        self._default_claims = config.get("default_claims") or {}
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
        return [
            {
                "path": "/claim_task",
                "endpoint": self.claim_api,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "直接领取指定任务",
                "description": "重新校验任务 ID、站点及领取条件后直接提交领取",
            },
            {
                "path": "/default_task",
                "endpoint": self.set_default_api,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "设置或取消站点默认任务",
                "description": "只保存或取消默认任务配置，不提交站点领取请求",
            },
        ]

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
        jobs = []
        if self._enabled and self._cron:
            try:
                jobs.append({
                    "id": "TaskHelper",
                    "name": "PT任务助手-定时刷新任务",
                    "trigger": CronTrigger.from_crontab(self._cron),
                    "func": self.refresh,
                    "kwargs": {},
                })
            except ValueError as err:
                logger.error(f"[任务助手] Cron 配置无效：{err}")
        if self._enabled and self._default_claim_cron:
            try:
                jobs.append({
                    "id": "TaskHelperDefaultClaim",
                    "name": "PT任务助手-高频默认领取",
                    "trigger": CronTrigger.from_crontab(self._default_claim_cron),
                    "func": self.run_default_claims,
                    "kwargs": {},
                })
            except ValueError as err:
                logger.error(f"[任务助手] 默认领取 Cron 配置无效：{err}")
        return jobs

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "default_claim_cron": self._default_claim_cron,
            "default_claims": self._default_claims,
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

    @staticmethod
    def _sites_by_id() -> Dict[str, Dict]:
        return {
            str(site.get("id")): site
            for site in SitesHelper().get_indexers()
        }

    @staticmethod
    def _tasks_with_id(tasks: List[Dict], task_id: Any) -> List[Dict]:
        try:
            wanted = int(task_id)
        except (TypeError, ValueError):
            return []
        return [
            task for task in tasks
            if task.get("exam_id") is not None
            and int(task["exam_id"]) == wanted
        ]

    @classmethod
    def _task_by_id(cls, tasks: List[Dict], task_id: Any) -> Optional[Dict]:
        matches = cls._tasks_with_id(tasks, task_id)
        return matches[0] if len(matches) == 1 else None

    def _default_condition(self, task: Dict, data: Dict,
                           now: Optional[datetime] = None) -> Tuple[str, str]:
        """Return current default eligibility without claim-state constraints."""
        now = now or self._now().replace(tzinfo=None)
        hourly = data.get("hourly_magic") or 0
        user_level = data.get("user_level") or ""
        join_days = data.get("join_days")
        candidate = dict(task)
        candidate["checks"] = list(task.get("checks") or [])
        state = evaluate_task(
            candidate, hourly, now, user_level, join_days,
            ignore_capacity=True,
            ignore_claimed=True,
            require_action=False,
        )
        return state, candidate.get("reason") or state

    def _fetch_site_tasks(self, site: Dict) -> List[Dict]:
        """Refetch the task page so an old task ID can never receive a new task."""
        site_id = str(site.get("id"))
        previous = self.get_data(f"site_{site_id}") or {}
        handler = self._handler_for(site.get("url") or "")
        html = self._get_page(site, handler.task_path)
        if html is None:
            return []
        return handler.parse_tasks(
            html,
            self._now().replace(tzinfo=None),
            previous.get("hourly_magic") or 0,
            user_level=previous.get("user_level") or "",
            join_days=previous.get("join_days"),
        )

    def _request_claim(self, site: Dict, exam_id: int) -> Tuple[bool, str]:
        """Submit one claim with the verified numeric task ID."""
        try:
            wanted = int(exam_id)
        except (TypeError, ValueError):
            return False, "任务 ID 无效"
        base = self._base_url(site)
        response = RequestUtils(
            cookies=site.get("cookie"),
            ua=site.get("ua"),
            proxies=settings.PROXY if site.get("proxy") else None,
            timeout=self._timeout,
        ).post_res(
            url=urljoin(base + "/", self.TASK_AJAX_PATH.lstrip("/")),
            data={"action": "claimTask", "params[exam_id]": wanted},
        )
        if response is None:
            return False, "领取请求无响应"
        if response.status_code != 200:
            return False, f"领取请求失败：HTTP {response.status_code}"
        try:
            result = response.json()
        except Exception as err:
            return False, f"领取响应解析失败：{err}"
        message = result.get("msg") or result.get("message") or ""
        if result.get("ret") == 0:
            logger.info(
                f"[任务助手] {site.get('name')} 任务 ID={wanted} 领取成功："
                f"{message or '无返回消息'}"
            )
            return True, message or "领取成功"
        logger.warning(
            f"[任务助手] {site.get('name')} 任务 ID={wanted} 领取失败："
            f"ret={result.get('ret')} {message or '无返回消息'}"
        )
        return False, message or f"领取失败（ret={result.get('ret')}）"

    def _save_feedback(self, success: bool, message: str,
                       site_name: str = "", task_name: str = "") -> None:
        self.save_data("last_action", {
            "success": success,
            "message": message,
            "site_name": site_name,
            "task_name": task_name,
            "time": self._now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    def _persist_default_claims(self) -> None:
        self.__update_config()

    def _mark_claimed(self, site_id: str, exam_id: int) -> None:
        key = f"site_{site_id}"
        data = self.get_data(key)
        if not data:
            return
        tasks = data.get("tasks") or []
        target = self._task_by_id(tasks, exam_id)
        if not target:
            return
        target["claimed"] = True
        target["claimable"] = False
        target["state"] = "已领取"
        target["reason"] = "该任务已被你领取"
        apply_site_lock(tasks)
        data["tasks"] = tasks
        self.save_data(key, data)

    def claim_api(self, data: Dict[str, Any]) -> Dict[str, Any]:
        site_name = ""
        task_name = ""
        try:
            operation = data.get("action")
            if operation != "claim":
                message = f"操作类型不匹配：领取接口收到 {operation or '空操作'}"
                logger.warning(f"[任务助手] {message}")
                self._save_feedback(False, message)
                return self._api_failure(message)
            site_id = str(data.get("site_id") or "")
            task_id = data.get("task_id")
            if site_id not in {str(value) for value in self._site_ids}:
                return self._api_failure("该站点不在当前插件站点列表中")
            site = self._sites_by_id().get(site_id)
            if not site:
                return self._api_failure("未找到对应站点")
            site_name = site.get("name") or site_id
            tasks = self._fetch_site_tasks(site)
            if len(self._tasks_with_id(tasks, task_id)) > 1:
                message = f"任务 ID {task_id} 在当前页面重复，已拒绝领取以避免领错任务"
                self._save_feedback(False, message, site_name)
                return self._api_failure(message)
            target = self._task_by_id(tasks, task_id)
            if not target:
                message = f"任务 ID {task_id} 不存在于站点当前任务页面，已拒绝领取"
                self._save_feedback(False, message, site_name)
                return self._api_failure(message)
            task_name = target.get("name") or str(task_id)
            site_data = self.get_data(f"site_{site_id}") or {}
            candidate = dict(target)
            candidate["checks"] = list(target.get("checks") or [])
            claim_state = evaluate_task(
                candidate,
                site_data.get("hourly_magic") or 0,
                self._now().replace(tzinfo=None),
                site_data.get("user_level") or "",
                site_data.get("join_days"),
                ignore_capacity=True,
            )
            if claim_state != CLAIMABLE_STATE:
                message = (
                    f"任务《{task_name}》当前不可直接领取："
                    f"{candidate.get('reason') or claim_state or '条件不满足'}"
                )
                self._save_feedback(False, message, site_name, task_name)
                return self._api_failure(message)
            success, message = self._request_claim(site, target["exam_id"])
            if success:
                self._mark_claimed(site_id, target["exam_id"])
                self.refresh(site_ids=[site_id])
            feedback = (
                f"已直接领取《{task_name}》：{message}"
                if success
                else f"直接领取《{task_name}》失败：{message}"
            )
            self._save_feedback(success, feedback, site_name, task_name)
            return {
                "success": success,
                "message": feedback,
                "task_id": target["exam_id"],
            }
        except Exception as err:
            logger.exception("[任务助手] 直接领取异常")
            message = f"直接领取异常：{err}"
            self._save_feedback(False, message, site_name, task_name)
            return self._api_failure(message)

    @staticmethod
    def _api_failure(message: str) -> Dict[str, Any]:
        return {"success": False, "message": message}

    def set_default_api(self, data: Dict[str, Any]) -> Dict[str, Any]:
        operation = data.get("action")
        if operation != "set_default":
            message = f"操作类型不匹配：默认任务接口收到 {operation or '空操作'}"
            logger.warning(f"[任务助手] {message}")
            self._save_feedback(False, message)
            return self._api_failure(message)
        site_id = str(data.get("site_id") or "")
        task_id = data.get("task_id")
        site = self._sites_by_id().get(site_id)
        site_data = self.get_data(f"site_{site_id}") or {}
        current = self._default_claims.get(site_id)
        current_id = current.get("task_id") if current else None
        site_name = site_data.get("site_name") or (site or {}).get("name") or site_id
        task_name = str(task_id)
        if current_id is not None:
            try:
                same_as_current = int(current_id) == int(task_id)
            except (TypeError, ValueError):
                same_as_current = False
            if same_as_current:
                task_name = current.get("task_name") or str(task_id)
                self._default_claims.pop(site_id, None)
                self._persist_default_claims()
                message = f"已取消《{task_name}》的默认任务（未提交领取）"
                self._save_feedback(True, message, site_name, task_name)
                return {"success": True, "message": message}
        if current:
            message = (
                f"站点「{site_name}」已设置默认任务《{current.get('task_name') or current_id}」；"
                "请先取消原默认任务，再设置其他任务"
            )
            self._save_feedback(False, message, site_name, task_name)
            return self._api_failure(message)

        tasks = site_data.get("tasks") or []
        if site:
            tasks = self._fetch_site_tasks(site)
            if not tasks:
                message = "无法重新获取站点任务页，暂不能设置默认领取"
                self._save_feedback(False, message, site_name)
                return self._api_failure(message)
        matches = self._tasks_with_id(tasks, task_id)
        if len(matches) > 1:
            message = f"任务 ID {task_id} 重复，无法设置默认领取"
            self._save_feedback(False, message, site_name)
            return self._api_failure(message)
        target = matches[0] if matches else None
        if not target:
            message = f"任务 ID {task_id} 不存在，无法设置默认领取"
            self._save_feedback(False, message, site_name)
            return self._api_failure(message)
        default_state, default_reason = self._default_condition(target, site_data)
        task_name = target.get("name") or str(task_id)
        if default_state != CLAIMABLE_STATE:
            message = (
                f"任务《{task_name}》当前不满足默认领取条件："
                f"{default_reason or '条件不满足'}"
            )
            self._save_feedback(False, message, site_name, task_name)
            return self._api_failure(message)

        self._default_claims[site_id] = {
            "task_id": int(target["exam_id"]),
            "task_name": task_name,
        }
        self._persist_default_claims()
        message = (
            f"已将《{task_name}》设为站点「{site_name}」的默认任务"
            "（仅保存配置，未提交领取）"
        )
        logger.info(f"[任务助手] {message}")
        self._save_feedback(True, message, site_name, task_name)
        return {
            "success": True,
            "message": message,
            "task_id": target["exam_id"],
        }

    def run_default_claims(self) -> Dict[str, int]:
        """Try cached defaults at high frequency without a task-page GET."""
        summary = {"sites": 0, "attempted": 0, "claimed": 0, "failed": 0, "skipped": 0}
        sites = self._sites_by_id()
        selected = {str(site_id) for site_id in self._site_ids}
        for site_id, default in self._default_claims.items():
            site_id = str(site_id)
            if site_id not in selected:
                continue
            snapshot = self.get_data(f"site_{site_id}") or {}
            if not snapshot:
                summary["skipped"] += 1
                continue
            summary["sites"] += 1
            try:
                task_id = default.get("task_id")
                tasks = snapshot.get("tasks") or []
                if any(task.get("claimed") for task in tasks):
                    summary["skipped"] += 1
                    continue
                target = self._task_by_id(tasks, task_id)
                if not target:
                    summary["skipped"] += 1
                    continue
                default_state, _ = self._default_condition(target, snapshot)
                if default_state != CLAIMABLE_STATE:
                    summary["skipped"] += 1
                    continue
                summary["attempted"] += 1
                site = sites.get(site_id)
                if not site:
                    summary["skipped"] += 1
                    continue
                success, message = self._request_claim(site, target["exam_id"])
                if success:
                    summary["claimed"] += 1
                    self._mark_claimed(site_id, target["exam_id"])
                    self.refresh(site_ids=[site_id])
                    logger.info(
                        f"[任务助手] {site.get('name')} 默认任务《{target.get('name')}》"
                        f"领取成功：{message}"
                    )
                else:
                    summary["failed"] += 1
                    logger.warning(
                        f"[任务助手] {site.get('name')} 默认任务《{target.get('name')}》"
                        f"领取失败，本次不重试：{message}"
                    )
            except Exception as err:
                summary["failed"] += 1
                logger.warning(
                    f"[任务助手] {site.get('name')} 默认任务执行异常，本次不重试：{err}"
                )
        self.save_data("last_default_claim", {
            **summary,
            "updated": self._now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        return summary

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

    def refresh(self, site_ids: Optional[List[Any]] = None):
        snapshots = {}
        sites = {str(site.get("id")): site for site in SitesHelper().get_indexers()}
        users = self._user_metadata()
        now = self._now().replace(tzinfo=None)
        summary = {"sites": 0, "tasks": 0, "actionable": 0, "errors": 0}

        for site_id in (site_ids if site_ids is not None else self._site_ids):
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
            snapshots[str(site_id)] = data
            summary["sites"] += 1
            summary["tasks"] += len(tasks)
            summary["actionable"] += sum(
                task["state"] == CLAIMABLE_STATE for task in tasks
            )
            logger.info(
                f"[任务助手] {data['site_name']}（{handler.__name__}）："
                f"{len(tasks)} 个任务，时魔 "
                f"{hourly if hourly is not None else '未获取'}"
            )

        self.save_data("last_run", summary)
        logger.info(f"[任务助手] 抓取完成：{summary}")
        return snapshots

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
                        self._field(6, cron_component, "cron", "任务信息刷新周期",
                                    placeholder="0 * * * *"),
                        self._field(6, cron_component, "default_claim_cron",
                                    "默认领取周期", placeholder="*/5 * * * *"),
                    ],
                },
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "tonal",
                        "density": "compact",
                        "text": "任务信息低频刷新，默认领取高频尝试。领取判断忽略"
                                "认领人数上限，由站点做最终判断；领取成功后立即刷新该站点，"
                                "失败则保留当前任务缓存。",
                    },
                },
            ],
        }], {
            "enabled": False,
            "onlyonce": False,
            "cron": "0 * * * *",
            "default_claim_cron": "*/5 * * * *",
            "default_claims": {},
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
            data["site_id"] = str(site_id)
            totals["sites"] += 1
            tasks = data.get("tasks") or []
            for task in tasks:
                task["state"] = evaluate_task(
                    task,
                    data.get("hourly_magic") or 0,
                    now,
                    data.get("user_level") or "",
                    data.get("join_days"),
                )
            apply_site_lock(tasks)
            totals["claimed"] += sum(task["state"] == "已领取" for task in tasks)
            totals["actionable"] += sum(
                task["state"] == CLAIMABLE_STATE for task in tasks
            )
            totals["tasks"] += len(tasks)
            cards.append(self._site_card(data, now))

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

        content = [self._title("📋 PT任务助手"), self._stats(totals, last_run)]
        last_action = self.get_data("last_action")
        if last_action:
            content.append({
                "component": "VSnackbar",
                "props": {
                    "model-value": True,
                    "timeout": 5000,
                    "location": "top",
                    "color": "success" if last_action.get("success") else "error",
                    "min-width": "320",
                },
                "content": [{
                    "component": "span",
                    "text": last_action.get("message") or "操作完成",
                }],
            })
            self.save_data("last_action", None)
        return [*content, *cards]

    def _site_card(self, data: Dict, now: datetime) -> dict:
        hourly = data.get("hourly_magic") or 0
        hourly_text = f"{hourly:g}/h" if data.get("hourly_found") and hourly > 0 else "未获取"
        site_id = str(data.get("site_id") or "")
        default = self._default_claims.get(site_id)
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
                *([
                    self._chip(
                        f"默认领取：{default.get('task_name') or default.get('task_id')}",
                        "green",
                    )
                ] if default else []),
            ],
        }]
        if data.get("error"):
            content.append({
                "component": "VAlert",
                "props": {"type": "warning", "variant": "tonal", "density": "compact",
                          "text": data["error"]},
            })
        for task in data.get("tasks") or []:
            default_state, default_reason = self._default_condition(task, data, now)
            content.append(self._task_card(
                task, site_id, default_state, default_reason,
            ))
        return {
            "component": "VCard",
            "props": {"variant": "flat", "class": "mb-4 pa-4"},
            "content": content,
        }

    def _task_card(self, task: Dict, site_id: str,
                   default_state: str, default_reason: str) -> dict:
        state = task.get("state") or "未知"
        colors = {
            CLAIMABLE_STATE: "#22c55e",
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
        exam_id = task.get("exam_id")
        current_default = self._default_claims.get(site_id)
        is_default = bool(
            current_default
            and current_default.get("task_id") is not None
            and exam_id is not None
            and int(current_default["task_id"]) == int(exam_id)
        )
        claim_enabled = state == CLAIMABLE_STATE and exam_id is not None
        default_enabled = is_default or (
            not current_default
            and default_state == CLAIMABLE_STATE
            and exam_id is not None
        )
        claim_props = {
            "color": "green" if claim_enabled else colors.get(state, "#64748b"),
            "variant": "flat",
            "size": "small",
            "disabled": not claim_enabled,
            "style": (
                "background-color:#22c55e;color:#fff;"
                if claim_enabled
                else "background-color:{};color:#fff;opacity:0.65;".format(
                    colors.get(state, "#64748b")
                )
            ),
        }
        default_props = {
            "color": "deep-purple",
            "variant": "outlined" if is_default else "flat",
            "size": "small",
            "disabled": not default_enabled,
            "style": "" if is_default else (
                "background-color:#7c3aed;color:#fff;"
                if default_enabled
                else "background-color:#7c3aed;color:#fff;opacity:0.65;"
            ),
        }
        if claim_enabled:
            claim_text = CLAIMABLE_STATE
            claim_tip = task.get("reason") or "全部条件满足，可直接领取"
        else:
            claim_text = state
            claim_tip = task.get("reason") or state
        if is_default:
            default_text = "取消默认"
        elif current_default:
            default_text = "已有默认任务"
        elif exam_id is None:
            default_text = "任务ID缺失"
        elif not default_enabled:
            default_text = default_state or "不可用"
        else:
            default_text = "设为默认"
        if is_default:
            default_tip = "取消后可为本站其他任务设置默认领取"
        elif current_default:
            default_tip = (
                f"本站已有默认任务《{current_default.get('task_name') or current_default.get('task_id')}》，"
                "请先取消后再改选"
            )
        elif default_state == CLAIMABLE_STATE:
            default_tip = "按默认领取周期直接领取该任务"
        else:
            default_tip = f"当前不满足任务条件：{default_reason}"
        action = [{
            "component": "VBtn",
            "props": default_props,
            "text": default_text,
            "events": {
                "click": {
                    "api": "plugin/TaskHelper/default_task",
                    "method": "POST",
                    "params": {
                        "action": "set_default",
                        "site_id": site_id,
                        "task_id": exam_id,
                    },
                }
            },
            "content": [{
                "component": "VTooltip",
                "props": {"activator": "parent", "location": "top"},
                "text": default_tip,
            }],
        }, {
            "component": "VBtn",
            "props": claim_props,
            "text": claim_text,
            "events": {
                "click": {
                    "api": "plugin/TaskHelper/claim_task",
                    "method": "POST",
                    "params": {
                        "action": "claim",
                        "site_id": site_id,
                        "task_id": exam_id,
                    },
                }
            },
            "content": [{
                "component": "VTooltip",
                "props": {"activator": "parent", "location": "top"},
                "text": claim_tip,
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
                                *([self._chip("默认领取", "#7c3aed")]
                                  if is_default else []),
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

    def _stats(self, totals: Dict, last_run: Dict) -> dict:
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
                         "text": f"可直接领取 {totals['actionable']}"},
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
