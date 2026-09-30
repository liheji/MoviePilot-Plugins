# -*- coding: utf-8 -*-
"""
北洋园PT开注助手（tjupthelper）
从北洋园PT论坛「PT站点开放注册（邀请）信息汇总」主题抓取开注公告，每日运行一次（失败自动重试），
帖子正文的站点名/站点地址/开注起止时间全部交给 OpenAI 兼容大模型识别（不再做本地字符匹配），
识别结果入库后按列表连续展示（开注过期折叠，点击加载），当前时间落在开注窗口时发送通知。
"""
import re
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.utils.http import RequestUtils

from .openai import OpenAi
from .parser import (
    apply_ai_items,
    build_batch_text,
    merge_records,
    parse_ai_response,
    parse_forum_page,
    scan_page,
    scan_page_limit,
    status_of,
    window_text,
    _coerce_dt,
)


class TjuptHelper(_PluginBase):
    # ==================== 插件元数据 ====================
    plugin_name = "北洋园PT开注助手"
    plugin_desc = (
        "每天从北洋园PT（tjupt.org）论坛「PT站点开放注册（邀请）信息汇总」主题抓取开注公告，"
        "由 OpenAI 兼容大模型识别站点名、站点地址与开注起止时间并入库，"
        "列表连续展示（开注过期折叠，点击加载），当前时间落在开注窗口时通知可注册站点。\n"
        "成功执行一次即当日完成，失败按设定重试。"
    )
    plugin_icon = "Tjupt_A.png"
    plugin_version = "1.2.0"
    plugin_author = "yilee"
    author_url = "https://github.com/yilee"
    plugin_config_prefix = "tjupthelper_"
    plugin_order = 30
    auth_level = 2

    # ==================== 常量 ====================
    FORUM_URL = "/forums.php?action=viewtopic&topicid={topic_id}&page={page}"

    # ==================== 运行时属性 ====================
    _scheduler: Optional[BackgroundScheduler] = None

    _enabled: bool = False
    _notify: bool = True
    _onlyonce: bool = False
    # 临时的一次性「重扫修正」（用户要求后续删除）：最多往前 3 页、不在已结束处停止，
    # 用于修正此前识别错误的历史记录，跑完自动关闭
    _rescan: bool = False

    _cron: str = "0 9 * * *"
    _topic_id: str = "15461"
    _max_pages: int = 5
    _retry_count: int = 3
    _retry_interval_min: int = 30

    # OpenAI 兼容大模型（内容识别全部走 AI）
    _openai: Optional[OpenAi] = None
    _ai_url: str = ""
    _ai_key: str = ""
    _ai_model: str = ""
    _ai_proxy: bool = False

    # ==================== 生命周期 ====================

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        self.stop_service()

        config = config or {}
        self._enabled = config.get("enabled", False)
        self._notify = config.get("notify", True)
        self._onlyonce = config.get("onlyonce", False)
        self._rescan = bool(config.get("rescan"))

        self._cron = config.get("cron") or "0 9 * * *"
        self._topic_id = str(config.get("topic_id") or "15461")
        self._max_pages = int(config.get("max_pages") or 5)
        self._retry_count = int(config.get("retry_count") or 3)
        self._retry_interval_min = int(config.get("retry_interval_min") or 30)

        self._ai_url = config.get("ai_url") or ""
        self._ai_key = config.get("ai_key") or ""
        self._ai_model = config.get("ai_model") or ""
        self._ai_proxy = bool(config.get("ai_proxy"))

        self._openai = None
        if self._ai_url and self._ai_key and self._ai_model:
            try:
                self._openai = OpenAi(
                    api_key=self._ai_key,
                    api_url=self._ai_url,
                    model=self._ai_model,
                    proxy=settings.PROXY if self._ai_proxy else None,
                )
                logger.info(f"[北洋园PT开注助手] 大模型加载完成：{self._ai_model}")
            except Exception as e:
                logger.error(f"[北洋园PT开注助手] 大模型初始化失败：{str(e)}")
        else:
            logger.warning(
                "[北洋园PT开注助手] 未配置大模型（OpenAI Base URL / API密钥 / 模型），开注信息无法识别"
            )

        # 自检：启用/立即运行前必须存在北洋园PT站点和可用大模型，否则自动关闭并告警
        if self._enabled or self._onlyonce or self._rescan:
            disabled = False
            if self._get_site_info(quiet=True) is None:
                logger.warning(
                    "[北洋园PT开注助手] 未检测到北洋园PT站点(tjupt.org)，"
                    "请先在站点管理中添加该站点及Cookie，插件已自动关闭"
                )
                disabled = True
            if not self._openai:
                logger.warning(
                    "[北洋园PT开注助手] 未配置可用的大模型（OpenAI Base URL / API密钥 / 模型），"
                    "开注信息全部依赖 AI 识别，插件已自动关闭"
                )
                disabled = True
            if disabled:
                self._enabled = False
                self._onlyonce = False
                self._rescan = False

        self.__update_config()

        if self._enabled or self._onlyonce or self._rescan:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            onlyonce = self._onlyonce
            if self._onlyonce:
                logger.info("北洋园PT开注助手服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self._run_daily,
                    kwargs={"force": True},
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                    name="北洋园PT开注助手",
                    id="tjupt_onlyonce",
                )
                self._onlyonce = False
                self.__update_config()
            if self._rescan and self._enabled and not onlyonce:
                logger.info("北洋园PT开注助手：重扫修正已开启，立即执行一次重扫（最多往前 3 页）")
                self._scheduler.add_job(
                    func=self._run_daily,
                    kwargs={"force": True},
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                    name="北洋园PT开注助手重扫修正",
                    id="tjupt_rescan",
                )
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        """退出插件"""
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown(wait=False)
                self._scheduler = None
        except Exception as e:
            logger.error(f"退出插件失败：{str(e)}")

    def get_service(self) -> List[Dict[str, Any]]:
        """注册每日定时任务"""
        if self._enabled and self._cron:
            return [{
                "id": "TjuptHelper.Daily",
                "name": "北洋园PT开注助手 - 每日抓取",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self._run_daily,
                "kwargs": {"force": False},
            }]
        return []

    # ==================== 配置持久化 ====================

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "notify": self._notify,
            "onlyonce": self._onlyonce,
            "rescan": self._rescan,
            "ai_proxy": self._ai_proxy,
            "cron": self._cron,
            "topic_id": self._topic_id,
            "ai_url": self._ai_url,
            "ai_key": self._ai_key,
            "ai_model": self._ai_model,
            "max_pages": self._max_pages,
            "retry_count": self._retry_count,
            "retry_interval_min": self._retry_interval_min,
        })

    # ==================== 站点匹配与请求 ====================

    @staticmethod
    def _match_tjupt(site: Dict) -> bool:
        url = site.get("url", "") or ""
        domain = site.get("domain", "") or ""
        return "tjupt.org" in url or "tjupt.org" in domain

    def _get_site_info(self, quiet: bool = False) -> Optional[Dict]:
        try:
            indexers = SitesHelper().get_indexers()
            for site in indexers:
                if self._match_tjupt(site):
                    logger.info(f"自动匹配到北洋园PT站点: {site.get('name')} ({site.get('url')})")
                    return site
        except Exception as e:
            logger.error(f"获取站点信息失败: {str(e)}")
            traceback.print_exc()
            return None
        if not quiet:
            logger.warning("未找到北洋园PT站点（tjupt.org），请在站点管理中添加该站点及Cookie")
        return None

    def _get_base_url(self, site_info: Dict) -> str:
        url = site_info.get("url", "") or ""
        if url:
            url = re.sub(r"/forums\.php.*$", "", url)
            url = re.sub(r"/attend.*$", "", url)
            url = url.rstrip("/")
        return url or "https://www.tjupt.org"

    def _make_get_request(self, site_info: Dict, url: str):
        cookies = site_info.get("cookie")
        ua = site_info.get("ua")
        proxies = settings.PROXY if site_info.get("proxy") else None
        return RequestUtils(
            cookies=cookies,
            ua=ua,
            proxies=proxies,
            timeout=30,
        ).get_res(url=url)

    def _fetch_forum_page(self, site_info: Dict, page: int, topic_id: str) -> Optional[str]:
        base_url = self._get_base_url(site_info)
        url = base_url + self.FORUM_URL.format(topic_id=topic_id, page=page)
        res = self._make_get_request(site_info, url)
        if res is None:
            logger.error(f"[开注] 抓取论坛页失败（无响应）: {url}")
            return None
        if res.status_code != 200:
            logger.error(f"[开注] 抓取论坛页失败: HTTP {res.status_code} {url}")
            return None
        return res.text

    def _get_last_page(self, site_info: Dict, topic_id: str) -> int:
        """通过抓取首页解析最大页号（page 为 0 起始索引）。"""
        html = self._fetch_forum_page(site_info, 0, topic_id)
        if html is None:
            return 0
        pages = [int(n) for n in re.findall(r"page=(\d+)", html)]
        last = max(pages) if pages else 0
        logger.info(f"[开注] 主题共 {last + 1} 页，最后一页索引 {last}")
        return last

    # ==================== 扫描与入库 ====================

    def _build_record(self, post: Dict, first_seen: str) -> Dict:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "pid": post.get("pid", ""),
            "post_no": post.get("post_no", ""),
            "post_url": post.get("post_url", ""),
            "username": post.get("username", ""),
            "user_en_name": post.get("user_en_name", ""),
            "post_time": post.get("post_time", ""),
            "site_name": post.get("site_name", ""),
            "site_name_raw": post.get("site_name_raw", ""),
            "site_url": post.get("site_url", ""),
            "reg_start": post.get("reg_start", ""),
            "reg_end": post.get("reg_end", ""),
            "end_inferred": bool(post.get("end_inferred")),
            "permanent": bool(post.get("permanent")),
            "ai_note": post.get("ai_note", ""),
            "ai_confidence": post.get("ai_confidence"),
            "content": (post.get("content") or "")[:500],
            "first_seen": first_seen,
            "updated": now_str,
        }

    @staticmethod
    def _status_of_record(record: Dict, now: datetime) -> str:
        """记录的开注状态（长期开放视为当前开注）。"""
        return status_of(
            _coerce_dt(record.get("reg_start")),
            _coerce_dt(record.get("reg_end")),
            now,
            permanent=bool(record.get("permanent")),
        )

    def _merge_records(self, active: Dict[str, Dict],
                       closed: Optional[Dict[str, Dict]] = None) -> Tuple[List[Dict], List[str]]:
        """
        合并入库：active 里的帖子新增或覆盖；closed 里的帖子只在库中已存在时覆盖
        （用于修正之前识别错误的历史记录），不新增。
        """
        existing = self.get_data("records") or []
        records, corrected = merge_records(existing, active, closed or {})
        self.save_data("records", records)
        if corrected:
            logger.info(f"[开注] 已修正 {len(corrected)} 条历史记录：{', '.join(corrected)}")
        return records, corrected

    def _ai_parse_page(self, posts: List[Dict], now: datetime) -> Tuple[bool, List[Dict], str]:
        """
        调用大模型识别一页帖子的开注信息，返回 (是否成功, 补齐字段后的帖子列表, 错误信息)。
        大模型内部已做「立即重试一次」，返回失败即代表本页放弃。
        """
        if not self._openai:
            return False, [], "未配置大模型"
        text = build_batch_text(posts)
        ok, result = self._openai.extract_open_reg(text, now)
        if not ok:
            return False, [], result
        parsed, items, err = parse_ai_response(result)
        if not parsed:
            return False, [], err
        return True, apply_ai_items(posts, items, now), ""

    def _scan(self) -> Tuple[bool, str]:
        site_info = self._get_site_info()
        if not site_info:
            return False, "未找到北洋园PT站点(tjupt.org)，请先在站点管理中添加"
        if not self._openai:
            return False, "未配置大模型（OpenAI Base URL / API密钥 / 模型）"

        topic_id = self._topic_id
        base_url = self._get_base_url(site_info)
        last_page = self._get_last_page(site_info, topic_id)
        now = datetime.now()
        rescan = self._rescan
        page_limit = scan_page_limit(self._max_pages, rescan)

        active: Dict[str, Dict] = {}
        closed: Dict[str, Dict] = {}
        pages_scanned = 0
        pages_skipped = 0

        for page in range(last_page, -1, -1):
            pages_scanned += 1
            if pages_scanned > page_limit:
                logger.info(f"[开注] 已达最大扫描页数 {page_limit}，停止继续往旧页扫描")
                break

            html = self._fetch_forum_page(site_info, page, topic_id)
            if html is None:
                return False, f"第 {page + 1} 页抓取失败"

            posts = parse_forum_page(
                html,
                topic_id=topic_id,
                base_url=base_url,
            )
            logger.info(f"[开注] 第 {page + 1} 页解析到 {len(posts)} 个帖子")
            if not posts:
                continue

            ok, parsed_posts, err = self._ai_parse_page(posts, now)
            if not ok:
                # 已在本页立即重试过一次，仍然失败则跳过该页，继续往旧页扫描
                pages_skipped += 1
                logger.warning(f"[开注] 第 {page + 1} 页 AI 识别失败，已跳过：{err}")
                continue

            # 页面内最新优先；方案A：遇到开注已结束的帖子即停止往更旧页面扫描
            # （重扫修正时不停止，把已结束的帖子也收上来修正历史记录；
            #   重扫是一次性临时功能，最多往前 RESCAN_MAX_PAGES 页）
            active_posts, closed_posts, stopped = scan_page(parsed_posts, now, stop_at_closed=not rescan)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for post in active_posts:
                pid = post.get("pid", "")
                if not pid:
                    continue
                active.setdefault(pid, self._build_record(post, now_str))
            for post in closed_posts:
                pid = post.get("pid", "")
                if not pid:
                    continue
                closed.setdefault(pid, self._build_record(post, now_str))
            if stopped:
                logger.info(f"[开注] 第 {page + 1} 页遇到已结束的开注公告，停止往更旧页面扫描")
                break

        _, corrected = self._merge_records(active, closed)

        if rescan:
            self._rescan = False
            self.__update_config()
            logger.info("[开注] 重扫已完成，已自动关闭该开关")

        if not active:
            logger.info("[开注] 本次未发现任何开放中/即将开放的开注公告，但仍视为抓取成功")
        msg = f"处理完成：扫描 {pages_scanned} 页，更新 {len(active)} 条开注公告"
        if corrected:
            msg += f"，修正 {len(corrected)} 条历史记录"
        if pages_skipped:
            msg += f"，{pages_skipped} 页因 AI 识别失败被跳过"
        return True, msg

    # ==================== 每日运行与重试 ====================

    def _ensure_scheduler(self) -> BackgroundScheduler:
        if not self._scheduler:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
        if not self._scheduler.running:
            self._scheduler.start()
        return self._scheduler

    def _run_daily(self, force: bool = False):
        today = datetime.now().strftime("%Y-%m-%d")
        if not force and self.get_data(f"done_{today}"):
            logger.info(f"[开注] 今日({today})已成功执行过，跳过")
            return

        ok, msg = self._scan()
        if ok:
            self.save_data(f"done_{today}", True)
            logger.info(f"[开注] 今日执行成功：{msg}")
            self._notify_open_sites()
        else:
            logger.warning(f"[开注] 今日执行失败：{msg}")
            self._schedule_retry(today)

    def _schedule_retry(self, today: str):
        state = self.get_data("retry_state") or {}
        if state.get("date") != today:
            state = {"date": today, "count": 0}
        count = state.get("count", 0)
        if count >= self._retry_count:
            logger.error(f"[开注] 今日失败重试已达上限 {self._retry_count} 次，放弃")
            return
        count += 1
        state["count"] = count
        self.save_data("retry_state", state)

        scheduler = self._ensure_scheduler()
        run_date = datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(minutes=self._retry_interval_min)
        scheduler.add_job(
            func=self._run_daily,
            kwargs={"force": False},
            trigger="date",
            run_date=run_date,
            name=f"北洋园PT开注助手重试{count}",
            id=f"tjupt_retry_{today}_{count}",
            replace_existing=True,
        )
        logger.info(f"[开注] 已安排第 {count} 次重试（{self._retry_interval_min}分钟 后）")

    # ==================== 通知 ====================

    def _notify_open_sites(self):
        if not self._notify:
            return
        records = self.get_data("records") or []
        now = datetime.now()
        open_sites = []
        for r in records:
            st = self._status_of_record(r, now)
            if st == "open":
                open_sites.append(r)

        notified = set(self.get_data("notified_pids") or [])
        new_open = [r for r in open_sites if r.get("pid") not in notified]
        if not new_open:
            logger.info("[开注] 无新增可注册站点，不发送通知")
            return

        title = "【北洋园PT开注助手】当前可注册站点"
        text = "\n".join(
            f"■ {r.get('site_name') or r.get('pid')}\n"
            f"地址：{r.get('site_url') or '-'}\n"
            f"开注时间：{window_text(r)}"
            for r in new_open
        )
        try:
            self.post_message(mtype=NotificationType.SiteMessage, title=title, text=text)
            self.save_data("notified_pids", sorted(notified | {r.get("pid") for r in new_open}))
            logger.info(f"[开注] 已发送通知，共 {len(new_open)} 个可注册站点")
        except Exception as e:
            logger.error(f"[开注] 发送通知失败：{str(e)}")

    # ==================== 插件 API ====================

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/records",
                "endpoint": self._api_records,
                "methods": ["GET"],
                "summary": "查询开注记录",
                "description": "返回全部开注公告记录，可选 status=open|upcoming|closed 过滤",
                "auth": "bear",
            },
            {
                "path": "/refresh",
                "endpoint": self._api_refresh,
                "methods": ["GET"],
                "summary": "手动运行一次",
                "description": "立即执行一次开注信息抓取（忽略当日成功标记）",
                "auth": "bear",
            },
        ]

    def _api_records(self, status: str = ""):
        records = self.get_data("records") or []
        if status:
            now = datetime.now()
            records = [
                r for r in records
                if self._status_of_record(r, now) == status
            ]
        return {
            "total": len(records),
            "records": records,
        }

    def _api_refresh(self):
        self._run_daily(force=True)
        return {"status": "done"}

    # ==================== 配置页面 ====================

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        version = getattr(settings, "VERSION_FLAG", "v1")
        cron_field = "VCronField" if version == "v2" else "VTextField"

        def switch(cols_sm: int, model: str, label: str) -> dict:
            return {
                "component": "VCol",
                "props": {"cols": 12, "sm": cols_sm},
                "content": [{"component": "VSwitch",
                             "props": {"model": model, "label": label, "color": "primary",
                                       "hide-details": True}}],
            }

        def field(cols_sm: int, model: str, label: str, component: str = "VTextField", **extra) -> dict:
            return {
                "component": "VCol",
                "props": {"cols": 12, "sm": cols_sm},
                "content": [{
                    "component": component,
                    "props": {"model": model, "label": label, "hide-details": True, **extra},
                }],
            }

        return [{
            "component": "VForm",
            "content": [
                # 首行：仅开关（单选放前面）
                {
                    "component": "VRow",
                    "content": [
                        switch(3, "enabled", "启用插件"),
                        switch(3, "notify", "开注通知"),
                        switch(3, "onlyonce", "立即运行一次"),
                        switch(3, "ai_proxy", "AI走代理"),
                    ],
                },
                # 第二行：重扫修正（一次性临时功能，跑完自动关闭）
                {
                    "component": "VRow",
                    "content": [
                        switch(6, "rescan", "重扫修正(历史识别错误)"),
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "sm": 6},
                            "content": [{
                                "component": "VAlert",
                                "props": {"type": "warning", "variant": "tonal", "density": "compact",
                                          "text": "打开并保存后立即重扫一次（最多往前 3 页）：不因开注已过期而停止，"
                                                  "只重写库中已有记录的识别结果，已结束的注册绝不会新增进列表，"
                                                  "跑完自动关闭该开关（临时功能）。"},
                            }],
                        },
                    ],
                },
                # 之后：输入项
                {
                    "component": "VRow",
                    "content": [
                        field(6, "cron", "每日执行(Cron)", component=cron_field,
                              placeholder="0 9 * * *", variant="outlined"),
                        field(6, "topic_id", "主题ID", placeholder="15461", variant="outlined"),
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        field(6, "ai_url", "OpenAI Base URL",
                              placeholder="https://api.openai.com/v1", variant="outlined"),
                        field(6, "ai_key", "API密钥", placeholder="sk-xxx", variant="outlined"),
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        field(6, "ai_model", "自定义模型", placeholder="gpt-4o", variant="outlined"),
                        field(6, "max_pages", "最大扫描页数", type="number", variant="outlined"),
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        field(6, "retry_count", "失败重试次数", type="number", variant="outlined"),
                        field(6, "retry_interval_min", "重试间隔(分钟)", type="number", variant="outlined"),
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "sm": 12},
                            "content": [{
                                "component": "VAlert",
                                "props": {"type": "info", "variant": "tonal", "density": "compact",
                                          "text": "开注信息全部由 OpenAI 兼容大模型识别，"
                                                  "需填写 Base URL / API密钥 / 模型，否则插件会自动关闭；"
                                                  "AI 调用失败会立即重试一次，仍然失败则跳过该页并记录日志。"
                                                  "另需先在站点管理中添加北洋园PT(tjupt.org)站点及Cookie。"},
                            }],
                        },
                    ],
                },
            ],
        }], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "rescan": False,
            "ai_proxy": False,
            "cron": "0 9 * * *",
            "topic_id": "15461",
            "ai_url": "",
            "ai_key": "",
            "ai_model": "",
            "max_pages": 5,
            "retry_count": 3,
            "retry_interval_min": 30,
        }

    def get_page(self) -> List[dict]:
        records = self.get_data("records") or []
        if not records:
            return [{
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "暂无开注记录，等待定时任务执行或点击「立即运行」",
                    "class": "mt-4",
                },
            }]

        now = datetime.now()
        open_count = upcoming_count = closed_count = 0
        for r in records:
            st = self._status_of_record(r, now)
            if st == "open":
                open_count += 1
            elif st == "upcoming":
                upcoming_count += 1
            elif st == "closed":
                closed_count += 1

        page = [
            self._page_title("📊 北洋园PT开注助手"),
            self._stats_row(open_count, upcoming_count, closed_count, len(records)),
            self._section_header("🎯", "开注信息列表", "#16b1ff"),
            *self._records_list(records),
        ]
        return page

    @staticmethod
    def _page_title(text: str) -> dict:
        return {
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 12},
                "content": [{"component": "h3", "props": {"class": "text-h5 font-weight-bold mb-4"},
                             "text": text}],
            }],
        }

    @staticmethod
    def _section_header(icon: str, title: str, color: str) -> dict:
        return {
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 12},
                "content": [{
                    "component": "div",
                    "props": {"class": "d-flex align-center mb-2 mt-2"},
                    "content": [
                        {"component": "span", "props": {"class": "text-h6 mr-2"}, "text": icon},
                        {"component": "span", "props": {"class": "text-h6 font-weight-bold",
                                                        "style": f"color: {color}"}, "text": title},
                    ],
                }],
            }],
        }

    @staticmethod
    def _stats_row(open_count, upcoming_count, closed_count, total) -> dict:
        stats = [
            {"label": "当前可注册", "value": open_count, "color": "#22c55e", "icon": "mdi-check-circle"},
            {"label": "即将开放", "value": upcoming_count, "color": "#f59e0b", "icon": "mdi-clock-outline"},
            {"label": "已结束", "value": closed_count, "color": "#94a3b8", "icon": "mdi-close-circle"},
            {"label": "总记录", "value": total, "color": "#16b1ff", "icon": "mdi-database"},
        ]
        cols = []
        for s in stats:
            cols.append({
                "component": "VCol",
                "props": {"cols": 6, "sm": 3},
                "content": [{
                    "component": "VCard",
                    "props": {"variant": "flat", "class": "mb-2", "color": "#f8fafc"},
                    "content": [{
                        "component": "VCardText",
                        "props": {"class": "pa-3 text-center"},
                        "content": [
                            {"component": "div", "props": {"class": "text-caption text-grey text-uppercase mb-1"},
                             "text": s["label"]},
                            {"component": "div", "props": {"class": "text-h6 font-weight-bold",
                                                           "style": f"color: {s['color']}"}, "text": str(s["value"])},
                        ],
                    }],
                }],
            })
        return {"component": "VRow", "content": cols}

    def _records_list(self, records: List[Dict]) -> List[dict]:
        """非分页：连续展示「当前开注/即将开注」；「开注过期」折叠为可点击加载的分组。"""
        now = datetime.now()
        active, closed = [], []
        for r in records:
            st = self._status_of_record(r, now)
            if st == "closed":
                closed.append(r)
            else:
                active.append(r)
        parts = []
        if active:
            parts.extend(self._records_grid(active))
        if closed:
            parts.append(self._closed_panel(closed))
        return parts

    def _closed_panel(self, closed: List[Dict]) -> dict:
        """「开注过期」折叠面板：默认折叠，点击展开才显示（近似异步加载）。"""
        return {
            "component": "VExpansionPanels",
            "props": {"variant": "accordion", "class": "elevation-0"},
            "content": [{
                "component": "VExpansionPanel",
                "props": {"class": "mt-3", "style": "background: #fff8f6; border: 1px solid #f3d9d5;"},
                "content": [
                    {
                        "component": "VExpansionPanelTitle",
                        "props": {"class": "pa-3", "style": "min-height: 44px;"},
                        "content": [
                            {"component": "VIcon", "props": {"color": "#ef4444", "size": "20", "class": "mr-2"},
                             "text": "mdi-download"},
                            {"component": "span", "props": {"class": "text-body-1 font-weight-medium",
                                                            "style": "color: #ef4444;"},
                             "text": f"开注过期（点击加载 {len(closed)} 条）"},
                        ],
                    },
                    {
                        "component": "VExpansionPanelText",
                        "props": {"class": "py-2"},
                        "content": self._records_grid(closed),
                    },
                ],
            }],
        }

    def _records_grid(self, records: List[Dict]) -> List[dict]:
        cards = [self._record_card(r) for r in records]
        return [{"component": "VRow", "content": cards}]

    def _record_card(self, r: Dict) -> dict:
        now = datetime.now()
        st = self._status_of_record(r, now)
        color, label, icon = {
            "open": ("#22c55e", "当前开注", "mdi-check-circle"),
            "upcoming": ("#f59e0b", "即将开注", "mdi-clock-outline"),
            "closed": ("#ef4444", "开注过期", "mdi-close-circle"),
            "unknown": ("#94a3b8", "状态未知", "mdi-help-circle"),
        }.get(st, ("#94a3b8", "状态未知", "mdi-help-circle"))

        return {
            "component": "VCol",
            "props": {"cols": 12},
            "content": [{
                "component": "VCard",
                "props": {"variant": "flat", "class": "mb-3",
                          "style": "border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); border: 1px solid #edeff2; margin-bottom: 0 !important;"},
                "content": [
                    {"component": "VCardText", "props": {"class": "pa-4"},
                     "content": [{
                         "component": "VRow",
                         "props": {"align": "center", "no-gutters": True},
                         "content": [
                             {
                                 "component": "VCol",
                                 "props": {"cols": 12, "sm": 9},
                                 "content": [
                                     {
                                         "component": "div",
                                         "props": {"class": "d-flex align-center mb-2"},
                                         "content": [
                                             {"component": "VIcon", "props": {"color": color, "size": "22",
                                                                              "class": "mr-2"}, "text": icon},
                                             {"component": "span", "props": {"class": "text-h6 font-weight-bold",
                                                                             "style": "color: #1f2a37;"},
                                              "text": r.get("site_name") or r.get("site_name_raw") or r.get("pid")},
                                             {"component": "VChip", "props": {"color": color, "size": "small",
                                                                              "variant": "flat", "class": "ml-3"},
                                              "text": label},
                                         ],
                                     },
                                     {"component": "div",
                                      "props": {"class": "text-body-2 mb-1", "style": "color: #374151;"},
                                      "text": f"开注时间：{window_text(r)}"},
                                     {"component": "div",
                                      "props": {"class": "text-caption mt-2", "style": "color: #9ca3af;"},
                                      "text": f"发布：{r.get('post_time') or '-'}  by {r.get('username') or '-'}"},
                                 ],
                             },
                             {
                                 "component": "VCol",
                                 "props": {"cols": 12, "sm": 3, "class": "text-sm-right"},
                                 "content": [
                                     {"component": "div",
                                      "content": [{"component": "VBtn",
                                                   "props": {"color": "primary", "variant": "outlined", "size": "small",
                                                             "href": r.get("site_url") or r.get("post_url") or "#",
                                                             "target": "_blank", "class": "px-2"},
                                                   "text": "访问站点"}]},
                                 ],
                             },
                         ],
                     }],
                     },
                ],
            }],
        }
