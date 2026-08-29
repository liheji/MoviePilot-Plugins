# -*- coding: utf-8 -*-
"""
北洋园PT开注助手（tjupthelper）
从北洋园PT论坛「PT站点开放注册（邀请）信息汇总」主题抓取开注公告，每日运行一次（失败自动重试），
解析开注起止时间并入库，插件页面按列表连续展示（开注过期折叠，点击加载），当前时间落在开注窗口时发送通知。
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

from .parser import _coerce_dt, parse_forum_page, status_of


class TjuptHelper(_PluginBase):
    # ==================== 插件元数据 ====================
    plugin_name = "北洋园PT开注助手"
    plugin_desc = (
        "每天从北洋园PT（tjupt.org）论坛「PT站点开放注册（邀请）信息汇总」主题抓取开注公告，"
        "自动解析开注起止时间并入库，列表连续展示（开注过期折叠，点击加载），当前时间落在开注窗口时通知可注册站点。\n"
        "成功执行一次即当日完成，失败按设定重试。"
    )
    plugin_icon = "Tjupt_A.png"
    plugin_version = "1.0.0"
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

    _cron: str = "0 9 * * *"
    _topic_id: str = "15461"
    _max_pages: int = 5
    _retry_count: int = 3
    _retry_interval_min: int = 30

    # ==================== 生命周期 ====================

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        self.stop_service()

        config = config or {}
        self._enabled = config.get("enabled", False)
        self._notify = config.get("notify", True)
        self._onlyonce = config.get("onlyonce", False)

        self._cron = config.get("cron") or "0 9 * * *"
        self._topic_id = str(config.get("topic_id") or "15461")
        self._max_pages = int(config.get("max_pages") or 5)
        self._retry_count = int(config.get("retry_count") or 3)
        self._retry_interval_min = int(config.get("retry_interval_min") or 30)

        # 站点自检：启用/立即运行前必须存在北洋园PT站点，否则自动关闭并告警
        if self._enabled or self._onlyonce:
            if self._get_site_info(quiet=True) is None:
                logger.warning(
                    "[北洋园PT开注助手] 未检测到北洋园PT站点(tjupt.org)，"
                    "请先在站点管理中添加该站点及Cookie，插件已自动关闭"
                )
                self._enabled = False
                self._onlyonce = False

        self.__update_config()

        if self._enabled or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
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
            "cron": self._cron,
            "topic_id": self._topic_id,
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
            "content": (post.get("content") or "")[:500],
            "first_seen": first_seen,
            "updated": now_str,
        }

    def _merge_records(self, collected: Dict[str, Dict]) -> List[Dict]:
        existing = self.get_data("records") or []
        by_pid = {r.get("pid"): r for r in existing if r.get("pid")}
        for pid, rec in collected.items():
            old = by_pid.get(pid)
            rec["first_seen"] = old.get("first_seen") if old else rec["first_seen"]
            by_pid[pid] = rec
        records = sorted(by_pid.values(), key=lambda r: r.get("post_time") or "", reverse=True)
        self.save_data("records", records)
        return records

    def _scan(self) -> Tuple[bool, str]:
        site_info = self._get_site_info()
        if not site_info:
            return False, "未找到北洋园PT站点(tjupt.org)，请先在站点管理中添加"

        topic_id = self._topic_id
        base_url = self._get_base_url(site_info)
        last_page = self._get_last_page(site_info, topic_id)
        now = datetime.now()

        collected: Dict[str, Dict] = {}
        terminated = False
        pages_scanned = 0

        for page in range(last_page, -1, -1):
            pages_scanned += 1
            if pages_scanned > self._max_pages:
                logger.info(f"[开注] 已达最大扫描页数 {self._max_pages}，停止继续往旧页扫描")
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

            # 页面内最新优先（帖子在页面内由旧到新排列）
            for post in reversed(posts):
                reg_start = _coerce_dt(post.get("reg_start"))
                reg_end = _coerce_dt(post.get("reg_end"))
                # 方案A：遇到注册窗口已完全结束的帖子 -> 停止扫描更旧页面
                if reg_end and reg_end < now:
                    logger.info(
                        f"[开注] PID {post.get('pid')} 注册窗口已结束({post.get('reg_end')})，停止扫描"
                    )
                    terminated = True
                    break
                if reg_start or reg_end:
                    pid = post.get("pid", "")
                    if not pid:
                        continue
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    collected.setdefault(pid, self._build_record(post, now_str))

            if terminated:
                break

        if not collected:
            logger.info("[开注] 本次未发现任何开注公告，但仍视为抓取成功")
        self._merge_records(collected)
        return True, f"处理完成：扫描 {pages_scanned} 页，更新 {len(collected)} 条开注公告"

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
            st = status_of(_coerce_dt(r.get("reg_start")), _coerce_dt(r.get("reg_end")), now)
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
            f"开注时间：{r.get('reg_start') or '-'} ~ {r.get('reg_end') or '-'}"
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
                if status_of(_coerce_dt(r.get("reg_start")), _coerce_dt(r.get("reg_end")), now) == status
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
                        switch(4, "enabled", "启用插件"),
                        switch(4, "notify", "开注通知"),
                        switch(4, "onlyonce", "立即运行一次"),
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
                        field(4, "max_pages", "最大扫描页数", type="number", variant="outlined"),
                        field(4, "retry_count", "失败重试次数", type="number", variant="outlined"),
                        field(4, "retry_interval_min", "重试间隔(分钟)", type="number", variant="outlined"),
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
                                          "text": "需先在站点管理中添加北洋园PT(tjupt.org)站点及Cookie。"
                                                  "成功执行一次即当日完成，失败按设定重试。"},
                            }],
                        },
                    ],
                },
            ],
        }], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "cron": "0 9 * * *",
            "topic_id": "15461",
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
            st = status_of(_coerce_dt(r.get("reg_start")), _coerce_dt(r.get("reg_end")), now)
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
            st = status_of(_coerce_dt(r.get("reg_start")), _coerce_dt(r.get("reg_end")), now)
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
        st = status_of(_coerce_dt(r.get("reg_start")), _coerce_dt(r.get("reg_end")), now)
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
                                      "text": f"开注时间：{r.get('reg_start') or '-'} ~ {r.get('reg_end') or '-'}"},
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
