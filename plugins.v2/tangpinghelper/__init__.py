import re
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, List, Dict, Tuple, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.utils.http import RequestUtils


class TangPingHelper(_PluginBase):
    # ==================== 插件元数据 ====================
    plugin_name = "躺平PT助手"
    plugin_desc = "躺平PT自动领红包、抽奖累加器与任务领取。\n支持自动领取红包、循环抽奖、智能任务领取三个独立功能，每日统计数据按天清除。"
    plugin_icon = "tangping.png"
    plugin_version = "1.0.0"
    plugin_author = "yilee"
    author_url = "https://github.com/yilee"
    plugin_config_prefix = "tangpinghelper_"
    plugin_order = 20
    auth_level = 2

    # ==================== 常量 ====================
    REDPACKET_LATEST_PATH = "/api/redpacket/latest"
    REDPACKET_CLAIM_PATH = "/api/redpacket/claim"
    LOTTERY_PAGE_PATH = "/omnibot_lottery.php"
    LOTTERY_DRAW_PATH = "/web/omnibot/lottery/draw"
    LOTTERY_DELAY = 5  # 固定每轮间隔（秒）

    # ==================== 私有属性 ====================
    _scheduler: Optional[BackgroundScheduler] = None

    # 配置属性
    _enabled: bool = False
    _notify: bool = False

    # 各功能独立开关
    _enabled_lottery: bool = False
    _enabled_redpacket: bool = False
    _enabled_task: bool = False

    # 各功能独立 cron
    _cron_lottery: str = ""
    _cron_redpacket: str = ""
    _cron_task: str = ""

    # 各功能独立「立即」按钮
    _onlyonce_lottery: bool = False
    _onlyonce_redpacket: bool = False
    _onlyonce_task: bool = False

    # 任务相关常量
    TASK_PAGE_PATH = "/task.php"
    TASK_AJAX_PATH = "/ajax.php"
    # 任务优先级：月底最后一天（距下月>2h）→ BUG > VIP > 苍蝇腿
    TASK_PRIORITY_EOM = ["BUG", "VIP", "苍蝇腿"]
    # 普通时间 → 只领苍蝇腿（VIP/BUG 仅限月末）
    TASK_PRIORITY_NORMAL = ["苍蝇腿"]

    # ==================== 生命周期 ====================

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        self.stop_service()

        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)

            self._enabled_lottery = config.get("enabled_lottery", False)
            self._enabled_redpacket = config.get("enabled_redpacket", False)
            self._enabled_task = config.get("enabled_task", False)

            self._cron_lottery = config.get("cron_lottery") or "0 */6 * * *"
            self._cron_redpacket = config.get("cron_redpacket") or "*/10 * * * *"
            self._cron_task = config.get("cron_task") or "0 */6 * * *"

            self._onlyonce_lottery = config.get("onlyonce_lottery", False)
            self._onlyonce_redpacket = config.get("onlyonce_redpacket", False)
            self._onlyonce_task = config.get("onlyonce_task", False)

            self.__update_config()

        # 检查是否需要立即运行
        any_onlyonce = self._onlyonce_lottery or self._onlyonce_redpacket or self._onlyonce_task
        if self._enabled or any_onlyonce:
            if any_onlyonce:
                # 在重置前捕获需要执行的模块
                do_lottery = self._onlyonce_lottery
                do_redpacket = self._onlyonce_redpacket
                do_task = self._onlyonce_task

                # 立即重置开关
                self._onlyonce_lottery = False
                self._onlyonce_redpacket = False
                self._onlyonce_task = False
                self.__update_config()

                # 调度执行（用闭包捕获的变量）
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                logger.info("躺平PT助手服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self._run_immediate,
                    kwargs={"do_lottery": do_lottery, "do_redpacket": do_redpacket, "do_task": do_task},
                    trigger='date',
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                    name="躺平PT助手"
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

    # ==================== 配置持久化 ====================

    def __update_config(self):
        """保存配置到持久化存储"""
        self.update_config({
            "enabled": self._enabled,
            "notify": self._notify,
            "enabled_lottery": self._enabled_lottery,
            "enabled_redpacket": self._enabled_redpacket,
            "enabled_task": self._enabled_task,
            "cron_lottery": self._cron_lottery,
            "cron_redpacket": self._cron_redpacket,
            "cron_task": self._cron_task,
            "onlyonce_lottery": self._onlyonce_lottery,
            "onlyonce_redpacket": self._onlyonce_redpacket,
            "onlyonce_task": self._onlyonce_task,
        })

    # ==================== 站点信息获取 ====================

    @staticmethod
    def _match_tangpt(site: Dict) -> bool:
        """判断站点是否为躺平PT（域名匹配 tangpt.top）"""
        url = site.get("url", "")
        domain = site.get("domain", "")
        return "tangpt.top" in url or "tangpt.top" in domain

    def _get_site_info(self) -> Optional[Dict]:
        """自动从站点助手匹配躺平PT站点（含 Cookie），无需手动选择"""
        try:
            indexers = SitesHelper().get_indexers()
            for site in indexers:
                if self._match_tangpt(site):
                    logger.info(f"自动匹配到躺平PT站点: {site.get('name')} ({site.get('url')})")
                    return site
        except Exception as e:
            logger.error(f"获取站点信息失败: {str(e)}")
            traceback.print_exc()
            return None

        logger.warning("未找到躺平PT站点（tangpt.top），请在站点管理中添加")
        return None

    def _get_base_url(self, site_info: Dict) -> str:
        """从站点信息提取 base URL"""
        url = site_info.get("url", "")
        if url:
            # 去除末尾斜杠和 attendance.php 等路径
            url = url.replace("attendance.php", "")
            url = url.rstrip("/")
        return url or "https://www.tangpt.top"

    def _make_get_request(self, site_info: Dict, path: str):
        """使用站点 Cookie 发起 GET 请求"""
        base_url = self._get_base_url(site_info)
        full_url = f"{base_url}{path}"
        cookies = site_info.get("cookie")
        ua = site_info.get("ua")
        proxies = settings.PROXY if site_info.get("proxy") else None

        return RequestUtils(
            cookies=cookies,
            ua=ua,
            proxies=proxies,
            timeout=30
        ).get_res(url=full_url)

    def _make_post_request(self, site_info: Dict, path: str, data: dict = None):
        """使用站点 Cookie 发起 POST 请求"""
        base_url = self._get_base_url(site_info)
        full_url = f"{base_url}{path}"
        cookies = site_info.get("cookie")
        ua = site_info.get("ua")
        proxies = settings.PROXY if site_info.get("proxy") else None

        headers = {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
        }

        return RequestUtils(
            cookies=cookies,
            ua=ua,
            proxies=proxies,
            headers=headers,
            timeout=30
        ).post_res(url=full_url, data=data)

    # ==================== 数据键（按天） ====================

    @staticmethod
    def _parse_daily_max(msg: str):
        """从服务端上限消息中提取每日最大数量，如 '每天最多领100个' → 100"""
        m = re.search(r'每天最多领\s*(\d+)\s*个', msg)
        return int(m.group(1)) if m else None

    def _today_key(self, prefix: str) -> str:
        """生成按天的存储键"""
        today = datetime.now().strftime("%Y-%m-%d")
        return f"{prefix}_{today}"

    def _clear_old_data(self):
        """清除非今日的统计数据"""
        prefixes = ["redpacket_stats", "lottery_stats", "task_stats"]
        for prefix in prefixes:
            # 获取所有相关键（通过尝试昨天及以前的模式）
            for days_ago in range(1, 32):
                old_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
                old_key = f"{prefix}_{old_date}"
                old_data = self.get_data(old_key)
                if old_data is not None:
                    self.del_data(old_key)
                    logger.debug(f"已清除旧数据: {old_key}")

    # ==================== 任务模块 ====================

    def _fetch_task_page(self, site_info: Dict) -> Optional[str]:
        """获取任务页面 HTML"""
        res = self._make_get_request(site_info, self.TASK_PAGE_PATH)
        if res is None or res.status_code != 200:
            logger.error(f"[任务] 获取任务页面失败: HTTP {res.status_code if res else '无响应'}")
            return None
        return res.text

    def _parse_tasks(self, html: str) -> List[Dict]:
        """
        解析任务页面 HTML，提取任务列表。
        返回: [{"name": str, "exam_id": int, "claimable": bool, "claimed": bool}, ...]
        """
        if not html:
            return []

        tasks = []
        # 匹配每个任务行：任务名 + 按钮信息
        pattern = re.compile(
            r'<td class="nowrap"><strong>([^<]+)</strong></td>'
            r'.*?'
            r'<input type="button" class="([^"]*)" data-id="(\d+)" value="([^"]*)"'
            r'(\s+disabled)?\s*>',
            re.DOTALL
        )

        for m in pattern.finditer(html):
            name = m.group(1).strip()
            btn_class = m.group(2).strip()
            exam_id = int(m.group(3))
            btn_value = m.group(4).strip()
            has_disabled = bool(m.group(5))  # 捕获 disabled 属性存在与否

            claimed = "已经认领" in btn_value
            claimable = btn_class == "claim" and not claimed and not has_disabled

            tasks.append({
                "name": name,
                "exam_id": exam_id,
                "claimable": claimable,
                "claimed": claimed,
            })
            logger.info(
                f"[任务] 发现任务: {name} (ID={exam_id}) "
                f"状态={'可领取' if claimable else '已认领' if claimed else '不可领取'}"
            )

        logger.info(f"[任务] 共解析到 {len(tasks)} 个任务，可领取 {sum(1 for t in tasks if t['claimable'])} 个")
        return tasks

    def _claim_task(self, site_info: Dict, exam_id: int) -> Tuple[bool, str]:
        """
        领取指定任务。
        返回: (成功与否, 消息)
        """
        data = {
            "action": "claimTask",
            "params[exam_id]": exam_id,
        }
        res = self._make_post_request(site_info, self.TASK_AJAX_PATH, data=data)

        if res is None:
            return False, "领取请求无响应"

        try:
            result = res.json()
        except Exception as e:
            return False, f"解析领取响应失败: {str(e)}"

        ret = result.get("ret")
        msg = result.get("msg", "")

        if ret == 0:
            logger.info(f"[任务] ✅ 任务 ID={exam_id} 领取成功: {msg}")
            return True, msg
        else:
            logger.warning(f"[任务] ❌ 任务 ID={exam_id} 领取失败: ret={ret} msg={msg}")
            return False, msg

    def _determine_task_priority(self) -> List[str]:
        """
        根据当前时间判断任务优先级列表。
        月末最后一天 → BUG > VIP > 苍蝇腿（距今日结束≥2h已在入口处检查）
        其他时间 → 只领苍蝇腿
        """
        now = datetime.now()
        tomorrow = now + timedelta(days=1)
        is_last_day_of_month = tomorrow.month != now.month

        if is_last_day_of_month:
            logger.info("[任务] 月底最后一天，优先级: BUG > VIP > 苍蝇腿")
            return self.TASK_PRIORITY_EOM
        else:
            logger.info("[任务] 非月底最后一天，优先级: 只领苍蝇腿")
            return self.TASK_PRIORITY_NORMAL

    def _run_task_claim(self, site_info: Dict) -> Dict:
        """
        自动领取任务核心逻辑。
        返回统计信息字典。
        """
        stats = {
            "claimed": False,
            "claimed_task": "",
            "claimed_id": None,
            "message": "",
            "already_claimed_today": False,
            "available_tasks": [],
            "errors": [],
        }

        # 检查今日是否已执行过（一天只能领取一个任务，无论成败）
        today_key = self._today_key("task_stats")
        today_task_data = self.get_data(today_key) or {}
        if today_task_data:
            logger.info(f"[任务] 今日已执行过「{today_task_data.get('claimed_task')}」，跳过")
            stats["already_claimed_today"] = True
            stats["claimed_task"] = today_task_data.get("claimed_task", "")
            stats["message"] = today_task_data.get("message", "")
            stats["claimed"] = today_task_data.get("claimed", False)
            return stats

        # 检查距今日结束是否 >= 2 小时（所有任务都要求此条件）
        now = datetime.now()
        end_of_today = datetime(now.year, now.month, now.day, 23, 59, 59)
        hours_until_eod = (end_of_today - now).total_seconds() / 3600
        if hours_until_eod < 2:
            logger.info(
                f"[任务] 距今日结束仅 {hours_until_eod:.1f}h < 2h，"
                f"不满足领取条件，跳过"
            )
            stats["message"] = f"距今日结束仅{hours_until_eod:.1f}h，不足2小时，跳过"
            return stats

        # 1. 获取任务页面
        logger.info("[任务] 获取任务列表...")
        html = self._fetch_task_page(site_info)
        if html is None:
            stats["errors"].append("获取任务页面失败")
            return stats

        # 2. 解析任务列表
        tasks = self._parse_tasks(html)
        if not tasks:
            stats["errors"].append("未解析到任何任务")
            return stats

        # 2a. 优先检查页面中是否已有已认领的任务（一天只能领取一个）
        already_claimed = [t for t in tasks if t["claimed"]]
        if already_claimed:
            selected = already_claimed[0]
            logger.info(f"[任务] 页面检测到已认领任务「{selected['name']}」，无需再次领取")
            self.save_data(today_key, {
                "claimed": True,
                "claimed_task": selected["name"],
                "claimed_id": selected["exam_id"],
                "message": "已认领（页面检测）",
                "errors": [],
                "claim_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            stats["claimed"] = True
            stats["claimed_task"] = selected["name"]
            stats["claimed_id"] = selected["exam_id"]
            stats["message"] = "已认领（页面检测）"
            return stats

        stats["available_tasks"] = [t["name"] for t in tasks if t["claimable"]]

        # 过滤可领取的任务
        claimable_map = {t["name"]: t for t in tasks if t["claimable"]}
        if not claimable_map:
            claimed_names = [t["name"] for t in tasks if t["claimed"]]
            logger.info(f"[任务] 没有可领取的任务，已认领: {claimed_names}")
            stats["message"] = "所有任务已认领"
            return stats

        # 3. 按优先级选择任务
        priorities = self._determine_task_priority()
        selected = None
        for target_name in priorities:
            if target_name in claimable_map:
                selected = claimable_map[target_name]
                logger.info(f"[任务] 按优先级选择: {target_name} (ID={selected['exam_id']})")
                break

        if selected is None:
            # 优先级中的任务都不可领取
            claimed_names = [t["name"] for t in tasks if t["claimed"]]
            logger.info(f"[任务] 优先级任务均不可领取（已认领: {claimed_names}），跳过")
            stats["message"] = f"优先级任务均不可领取"
            return stats

        # 4. 执行领取
        success, msg = self._claim_task(site_info, selected["exam_id"])

        stats["claimed"] = success
        stats["claimed_task"] = selected["name"]
        stats["claimed_id"] = selected["exam_id"]
        stats["message"] = msg

        if not success:
            stats["errors"].append(f"领取「{selected['name']}」失败: {msg}")

        # 5. 保存今日结果
        self.save_data(today_key, {
            "claimed": success,
            "claimed_task": selected["name"],
            "claimed_id": selected["exam_id"],
            "message": msg,
            "errors": stats["errors"],
            "claim_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        logger.info(f"[任务] 本次完成: {'✅' if success else '❌'} {selected['name']} - {msg}")
        return stats

    def _run_redpacket(self, site_info: Dict) -> Dict:
        """
        自动领红包核心逻辑。
        返回统计信息字典。
        """
        stats = {
            "claimed_count": 0,
            "total_magic": 0,
            "daily_limit_reached": False,
            "limit_reason": "",
            "errors": [],
        }

        # 加载今日已有累计（固定键，避免跨午夜不一致）
        date_key = self._today_key("redpacket_stats")
        today_stats = self.get_data(date_key) or {}
        claimed_count = today_stats.get("claimed_count", 0)
        total_magic = today_stats.get("total_magic", 0)
        daily_limit = today_stats.get("daily_limit_reached", False)
        daily_max = today_stats.get("daily_max", 0)

        # 今日已达上限（标记 / 累计达服务端上限 / 累计达100——脏数据恢复兜底）
        limit_reached = daily_limit or (daily_max > 0 and claimed_count >= daily_max) or claimed_count >= 100
        if limit_reached:
            logger.info("[红包] 今日已达领取上限，跳过")
            # 脏数据恢复：标记为 False 时，强制修复持久化
            if not daily_limit:
                self.save_data(date_key, {
                    "claimed_count": claimed_count,
                    "total_magic": total_magic,
                    "daily_limit_reached": True,
                    "daily_max": claimed_count,
                    "limit_reason": today_stats.get("limit_reason", ""),
                    "last_run": today_stats.get("last_run", ""),
                })
            return {
                "claimed_count": claimed_count,
                "total_magic": total_magic,
                "daily_limit_reached": True,
                "limit_reason": today_stats.get("limit_reason", ""),
                "last_run": today_stats.get("last_run", ""),
                "errors": [],
            }

        max_rounds = 50  # 安全上限，避免无限循环
        round_num = 0

        while round_num < max_rounds and not daily_limit:
            round_num += 1
            logger.info(f"[红包] 第 {round_num} 轮查询...")

            # 1. 获取最新红包
            res = self._make_get_request(site_info, self.REDPACKET_LATEST_PATH)
            if res is None:
                msg = "获取红包列表失败：无响应"
                logger.warning(f"[红包] {msg}")
                stats["errors"].append(msg)
                break

            if res.status_code != 200:
                msg = f"获取红包列表失败：HTTP {res.status_code}"
                logger.warning(f"[红包] {msg}")
                stats["errors"].append(msg)
                break

            try:
                data = res.json()
            except Exception as e:
                msg = f"解析红包响应失败: {str(e)}"
                logger.warning(f"[红包] {msg}")
                stats["errors"].append(msg)
                break

            if not data or not data.get("ok"):
                logger.info("[红包] 当前无可用红包，结束查询")
                break

            items = data.get("items", [])
            if not isinstance(items, list) or len(items) == 0:
                logger.info("[红包] 红包列表为空，结束查询")
                break

            # 2. 取第一个红包领取
            packet = items[0]
            packet_id = packet.get("id")
            sender = packet.get("sender", "未知")
            logger.info(f"[红包] 发现红包 ID={packet_id} 发送者={sender}")

            claim_res = self._make_post_request(
                site_info,
                self.REDPACKET_CLAIM_PATH,
                data={"packet_id": packet_id}
            )

            if claim_res is None:
                msg = f"领取红包 {packet_id} 失败：无响应"
                logger.warning(f"[红包] {msg}")
                stats["errors"].append(msg)
                break

            # 3. 处理领取结果
            if claim_res.status_code == 422:
                # 每日上限（HTTP 422）
                try:
                    resp_data = claim_res.json()
                    limit_msg = resp_data.get("message", "每日上限（HTTP 422）")
                except Exception:
                    limit_msg = "每日上限（HTTP 422）"
                logger.warning(f"[红包] 每日上限已达到: {limit_msg}")
                stats["daily_limit_reached"] = True
                stats["limit_reason"] = limit_msg
                stats["daily_max"] = self._parse_daily_max(limit_msg) or 100
                daily_limit = True
                break

            if claim_res.status_code != 200:
                msg = f"领取红包 {packet_id} 失败：HTTP {claim_res.status_code}"
                logger.warning(f"[红包] {msg}")
                stats["errors"].append(msg)
                # 短暂等待后继续
                time.sleep(1)
                continue

            try:
                claim_data = claim_res.json()
            except Exception as e:
                msg = f"解析领取响应失败: {str(e)}"
                logger.warning(f"[红包] {msg}")
                stats["errors"].append(msg)
                continue

            # 4. 判断领取结果
            if claim_data.get("ok"):
                magic = claim_data.get("magic_amount", 0)
                remain = claim_data.get("remain_count", 0)
                claimed_count += 1
                total_magic += magic
                logger.info(
                    f"[红包] ✅ 领取成功，获得魔力:{magic}，剩余次数:{remain}，累计:{claimed_count}个/{total_magic}魔力")

                if remain == 0:
                    logger.info("[红包] 剩余次数为0，结束本轮领取")
                    break
            else:
                msg = claim_data.get("message", "")
                logger.info(f"[红包] ⚠️ 领取返回异常: {msg}")

                if "每天最多领" in msg:
                    stats["daily_limit_reached"] = True
                    stats["limit_reason"] = msg
                    stats["daily_max"] = self._parse_daily_max(msg) or 100
                    daily_limit = True
                    break

                if any(kw in msg for kw in ["已经领取", "已达上限", "已过期"]):
                    logger.info(f"[红包] 红包 {packet_id} 已处理，继续下一轮")
                else:
                    stats["errors"].append(f"领取失败: {msg}")
                    break

            # 5. 间隔等待
            time.sleep(1.2)

        if round_num >= max_rounds:
            logger.warning("[红包] 达到最大轮次限制，强制停止")

        stats["claimed_count"] = claimed_count
        stats["total_magic"] = total_magic
        stats["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 保存今日累计数据（复用入口处固定的键）
        self.save_data(date_key, {
            "claimed_count": claimed_count,
            "total_magic": total_magic,
            "daily_limit_reached": today_stats.get("daily_limit_reached", False) or stats["daily_limit_reached"],
            "daily_max": stats.get("daily_max") or today_stats.get("daily_max", 0),
            "last_run": stats["last_run"],
        })

        logger.info(f"[红包] 本次完成：领取 {stats['claimed_count']} 个，魔力 +{stats['total_magic']}")
        return stats

    # ==================== 抽奖模块 ====================

    def _parse_summary_lines(self, lines):
        """
        动态解析 summary_lines，格式：key：数值 单位（注释）
        返回 {key: {"value": float, "unit": str}}
        """
        if not lines:
            return {}

        if isinstance(lines, str):
            lines = lines.split("\n")

        parsed = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 匹配 "key：数值 单位（可选注释）"
            m = re.match(r'^(.+?)[：:]\s*([\d.]+)\s*(\S+?)(?:\s*[（(]([^)]*)[)）])?\s*$', line)
            if m:
                label = m.group(1).strip()
                val = float(m.group(2))
                unit = m.group(3)
                if not parsed.get(label):
                    parsed[label] = {"value": 0, "unit": unit}
                parsed[label]["value"] += val
                continue

            # 兼容：已发放X个
            issued_m = re.match(r'已发放\s*([\d.]+)\s*个', line)
            if issued_m:
                key = "邀请已发放"
                if key not in parsed:
                    parsed[key] = {"value": 0, "unit": "个"}
                parsed[key]["value"] += float(issued_m.group(1))

        return parsed

    def _parse_lottery_remaining(self, html: str) -> int:
        """从抽奖页面 HTML 解析今日剩余可抽次数"""
        if not html:
            return -1
        # 优先从 JS 状态对象解析
        m = re.search(r'"dailyDrawRemaining":(\d+)', html)
        if m:
            remaining = int(m.group(1))
            logger.info(f"[抽奖] 今日剩余抽奖次数: {remaining}")
            return remaining
        # 备选：从页面文案解析
        m = re.search(r'今天还可以抽\s*(\d+)\s*次', html)
        if m:
            remaining = int(m.group(1))
            logger.info(f"[抽奖] 今日剩余抽奖次数(文案): {remaining}")
            return remaining
        logger.warning("[抽奖] 无法解析剩余抽奖次数")
        return -1

    def _run_lottery(self, site_info: Dict) -> Dict:
        """
        抽奖累加器核心逻辑。
        1. 先抓 omnibot_lottery.php 解析今日剩余次数
        2. 每次请求抽 100 次，循环直到 ok=false（次数用完）
        3. 固定间隔 5 秒
        """
        # 加载今日已有累计（固定键，避免跨午夜不一致）
        date_key = self._today_key("lottery_stats")
        today_stats = self.get_data(date_key) or {}
        total_draws = today_stats.get("total_draws", 0)
        total_cost = today_stats.get("total_cost", 0)
        total_compensated = today_stats.get("total_compensated", 0)
        total_awarded = today_stats.get("total_awarded", 0)
        summary_acc = today_stats.get("summary", {})

        stats = {
            "request_count": today_stats.get("request_count", 0),
            "total_draws": total_draws,
            "total_cost": total_cost,
            "total_compensated": total_compensated,
            "total_awarded": total_awarded,
            "first_bonus_after": today_stats.get("first_bonus_after"),
            "last_bonus_after": today_stats.get("last_bonus_after"),
            "summary": summary_acc,
            "net_change": total_awarded - total_cost,
            "errors": [],
            "stopped_early": False,
            "stop_reason": "",
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # 先获取抽奖页面，检查剩余次数
        logger.info("[抽奖] 获取抽奖页面...")
        page_html = None
        try:
            res = self._make_get_request(site_info, self.LOTTERY_PAGE_PATH)
            if res and res.status_code == 200:
                page_html = res.text
        except Exception as e:
            logger.warning(f"[抽奖] 获取抽奖页面失败: {str(e)}")

        remaining = self._parse_lottery_remaining(page_html or "")
        if remaining == 0:
            logger.info("[抽奖] 今日剩余抽奖次数为 0，跳过")
            stats["stop_reason"] = "今日次数已用完"
            return stats
        if remaining > 0:
            logger.info(f"[抽奖] 今日还可抽 {remaining} 次，开始循环")

        # 合法的抽奖档位（服务器仅接受这些值）
        VALID_DRAW_COUNTS = [100, 50, 20, 10, 1]

        i = 0
        while True:
            i += 1
            # 根据剩余次数自适应 count：选 ≤ remaining 的最大合法档位
            if remaining > 0:
                batch = next((c for c in VALID_DRAW_COUNTS if c <= remaining), 1)
            else:
                batch = 100
            logger.info(f"[抽奖] 第 {i} 次请求（count={batch}）...")

            res = self._make_post_request(
                site_info,
                self.LOTTERY_DRAW_PATH,
                data={"count": batch}
            )
            remaining = -1  # 仅首轮使用解析值

            if res is None:
                msg = f"第 {i} 次请求失败：无响应"
                logger.warning(f"[抽奖] {msg}")
                stats["errors"].append(msg)
                if len(stats["errors"]) >= 5:
                    stats["stopped_early"] = True
                    stats["stop_reason"] = "连续错误过多，停止抽奖"
                    break
                time.sleep(3)
                continue

            stats["request_count"] += 1

            try:
                data = res.json()
            except Exception as e:
                msg = f"第 {i} 次解析响应失败: {str(e)}"
                logger.warning(f"[抽奖] {msg}")
                stats["errors"].append(msg)
                stats["stopped_early"] = True
                stats["stop_reason"] = msg
                break

            ok = data.get("ok")

            if ok is True:
                draw_count = data.get("draw_count", 0)
                cost = data.get("total_cost", 0)
                compensated = data.get("total_compensated_bonus", 0)
                awarded = data.get("total_awarded_bonus", 0)
                bonus_after = data.get("user_bonus_after")

                total_draws += draw_count
                total_cost += cost
                total_compensated += compensated
                total_awarded += awarded

                if stats["first_bonus_after"] is None and bonus_after is not None:
                    stats["first_bonus_after"] = bonus_after
                if bonus_after is not None:
                    stats["last_bonus_after"] = bonus_after

                # 解析权益汇总
                summary_src = data.get("summary_lines") or data.get("summary_text")
                if summary_src:
                    parsed = self._parse_summary_lines(summary_src)
                    for key, item in parsed.items():
                        if key not in summary_acc:
                            summary_acc[key] = {"value": 0, "unit": item.get("unit", "")}
                        summary_acc[key]["value"] += item["value"]

                logger.info(
                    f"[抽奖] ✅ 第{i}次 抽奖{draw_count}次 "
                    f"消耗:{cost} 获得:{awarded} 补偿:{compensated}"
                )

            elif ok is False:
                fail_msg = data.get("message") or data.get("msg") or "服务器返回失败"
                logger.info(f"[抽奖] ⏹ 停止: {fail_msg}")

                if stats["last_bonus_after"] is None:
                    bonus_after = data.get("user_bonus_after")
                    if bonus_after is not None:
                        stats["last_bonus_after"] = bonus_after

                # 首次即失败 → 次数用完，静默退出
                if i == 1:
                    stats["stopped_early"] = True
                    stats["stop_reason"] = fail_msg
                    logger.info("[抽奖] 首次即失败（次数已用完），静默退出")
                    break

                stats["stopped_early"] = True
                stats["stop_reason"] = fail_msg
                break
            else:
                logger.warning(f"[抽奖] ⚠️ 未知响应状态 (ok={ok})，停止抽奖")
                stats["stopped_early"] = True
                stats["stop_reason"] = f"未知响应状态: ok={ok}"
                break

            # 固定 5 秒间隔
            time.sleep(self.LOTTERY_DELAY)

        stats["total_draws"] = total_draws
        stats["total_cost"] = total_cost
        stats["total_compensated"] = total_compensated
        stats["total_awarded"] = total_awarded
        stats["summary"] = summary_acc
        stats["end_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stats["net_change"] = total_awarded - total_cost

        # 保存今日累计数据（复用入口处固定的键）
        self.save_data(date_key, {
            "request_count": stats["request_count"],
            "total_draws": total_draws,
            "total_cost": total_cost,
            "total_compensated": total_compensated,
            "total_awarded": total_awarded,
            "first_bonus_after": stats["first_bonus_after"],
            "last_bonus_after": stats["last_bonus_after"],
            "summary": summary_acc,
            "net_change": stats["net_change"],
            "start_time": stats["start_time"],
            "end_time": stats["end_time"],
            "stop_reason": stats["stop_reason"],
        })

        logger.info(
            f"[抽奖] 本次完成：请求{stats['request_count']}次 "
            f"抽奖{total_draws}次 消耗{total_cost} 获得{total_awarded} "
            f"净变化{stats['net_change']:+}"
        )
        return stats

    # ==================== 统一调度 ====================

    def _run_immediate(self, do_lottery: bool = False, do_redpacket: bool = False, do_task: bool = False):
        """「立即」按钮调度入口，只执行被点击的模块"""
        try:
            self.__run_immediate_inner(do_lottery, do_redpacket, do_task)
        except Exception as e:
            logger.error(f"躺平PT助手执行异常: {str(e)}")
            traceback.print_exc()

    def __run_immediate_inner(self, do_lottery: bool, do_redpacket: bool, do_task: bool):
        logger.info("=" * 50)
        logger.info(
            f"躺平PT助手 立即执行: "
            f"抽奖={'✅' if do_lottery else '❌'} "
            f"红包={'✅' if do_redpacket else '❌'} "
            f"任务={'✅' if do_task else '❌'}"
        )

        try:
            self._clear_old_data()
        except Exception as e:
            logger.warning(f"清除旧数据时出错: {str(e)}")

        site_info = self._get_site_info()
        if not site_info:
            logger.error("⛔ 未找到躺平PT站点！")
            return

        site_name = site_info.get("name", "躺平PT")
        logger.info(f"使用站点: {site_name} (auto-matched)")

        redpacket_stats = None
        lottery_stats = None
        task_stats = None

        if do_redpacket:
            logger.info("[红包] 立即领取...")
            try:
                redpacket_stats = self._run_redpacket(site_info)
            except Exception as e:
                logger.error(f"[红包] 异常: {str(e)}")
                traceback.print_exc()

        if do_lottery:
            logger.info("[抽奖] 立即抽奖...")
            try:
                lottery_stats = self._run_lottery(site_info)
            except Exception as e:
                logger.error(f"[抽奖] 异常: {str(e)}")
                traceback.print_exc()

        if do_task:
            logger.info("[任务] 立即领取...")
            try:
                task_stats = self._run_task_claim(site_info)
            except Exception as e:
                logger.error(f"[任务] 异常: {str(e)}")
                traceback.print_exc()

        if self._notify and (redpacket_stats or lottery_stats or task_stats):
            self._send_notification(site_name, redpacket_stats, lottery_stats, task_stats)

        logger.info("躺平PT助手 立即执行完成")
        logger.info("=" * 50)

    def _send_notification(self, site_name: str, redpacket_stats: Dict = None,
                           lottery_stats: Dict = None, task_stats: Dict = None):
        """组装并发送通知消息"""
        parts = [f"【躺平PT助手】{site_name}\n"]

        if redpacket_stats:
            rp = redpacket_stats
            parts.append("🧧 红包领取:")
            parts.append(f"  · 本次领取: {rp.get('claimed_count', 0)} 个")
            parts.append(f"  · 获得魔力: {rp.get('total_magic', 0)}")
            if rp.get("daily_limit_reached"):
                parts.append(f"  · 状态: 已达到每日上限 ({rp.get('limit_reason', '')})")
            if rp.get("errors"):
                parts.append(f"  · 错误: {'; '.join(rp['errors'][:3])}")

        if lottery_stats:
            ls = lottery_stats
            parts.append("🎰 抽奖累加:")
            parts.append(f"  · 请求次数: {ls.get('request_count', 0)}")
            parts.append(f"  · 累计抽奖: {ls.get('total_draws', 0)} 次")
            parts.append(f"  · 消耗魔力: {ls.get('total_cost', 0):,}")
            parts.append(f"  · 获得魔力: {ls.get('total_awarded', 0):,}")
            nc = ls.get('net_change', 0)
            sign = "+" if nc >= 0 else ""
            parts.append(f"  · 净变化: {sign}{nc:,}")
            if ls.get("stop_reason"):
                parts.append(f"  · 停止原因: {ls['stop_reason']}")
            if ls.get("errors"):
                unique_errors = list(set(ls["errors"]))[:3]
                parts.append(f"  · 提示: {'; '.join(unique_errors)}")

        if task_stats:
            ts = task_stats
            parts.append("📋 任务领取:")
            if ts.get("already_claimed_today"):
                parts.append(f"  · 今日已领取: {ts.get('claimed_task', '-')}")
            elif ts.get("claimed"):
                parts.append(f"  · 领取任务: {ts.get('claimed_task', '-')} ✅")
                parts.append(f"  · 结果: {ts.get('message', '-')}")
            elif ts.get("errors"):
                parts.append(f"  · 错误: {'; '.join(ts['errors'][:3])}")
            else:
                parts.append(f"  · 状态: {ts.get('message', '无可领取任务')}")

        text = "\n".join(parts)
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title="【躺平PT助手】执行结果",
            text=text,
        )

    # ==================== 定时服务注册 ====================

    def _parse_cron(self, cron_str: str) -> Optional[CronTrigger]:
        """解析 cron 表达式，兼容 6 字段（自动去秒）"""
        if not cron_str or not cron_str.strip():
            return None
        try:
            parts = str(cron_str).strip().split()
            if len(parts) == 6:
                expr = " ".join(parts[1:])
            else:
                expr = str(cron_str).strip()
            return CronTrigger.from_crontab(expr)
        except Exception as e:
            logger.error(f"cron 解析失败 [{cron_str}]: {str(e)}")
            return None

    def get_service(self) -> List[Dict[str, Any]]:
        """注册插件定时服务——每个功能独立 cron"""
        if not self.get_state():
            return []
        services = []
        # 抽奖
        if self._enabled_lottery:
            trigger = self._parse_cron(self._cron_lottery)
            if trigger:
                services.append({
                    "id": "TangPingHelper.Lottery",
                    "name": "躺平PT-自动抽奖",
                    "trigger": trigger,
                    "func": self.run_lottery_service,
                    "kwargs": {}
                })
        # 红包
        if self._enabled_redpacket:
            trigger = self._parse_cron(self._cron_redpacket)
            if trigger:
                services.append({
                    "id": "TangPingHelper.Redpacket",
                    "name": "躺平PT-自动领红包",
                    "trigger": trigger,
                    "func": self.run_redpacket_service,
                    "kwargs": {}
                })
        # 任务
        if self._enabled_task:
            trigger = self._parse_cron(self._cron_task)
            if trigger:
                services.append({
                    "id": "TangPingHelper.Task",
                    "name": "躺平PT-自动领任务",
                    "trigger": trigger,
                    "func": self.run_task_service,
                    "kwargs": {}
                })
        return services

    def run_lottery_service(self):
        """定时服务：抽奖"""
        self._run_single("抽奖", self._run_lottery)

    def run_redpacket_service(self):
        """定时服务：红包"""
        self._run_single("红包", self._run_redpacket)

    def run_task_service(self):
        """定时服务：任务"""
        self._run_single("任务", self._run_task_claim)

    def _run_single(self, name: str, func):
        """执行单个模块并发送通知"""
        try:
            self._clear_old_data()
        except Exception as e:
            logger.warning(f"清除旧数据时出错: {str(e)}")

        site_info = self._get_site_info()
        if not site_info:
            logger.error(f"⛔ [{name}] 未找到站点")
            return
        logger.info(f"[{name}] 定时任务开始...")
        try:
            stats = func(site_info)
        except Exception as e:
            logger.error(f"[{name}] 执行异常: {str(e)}")
            traceback.print_exc()
            stats = {"errors": [str(e)]}
        if self._notify and stats:
            _key_map = {"抽奖": "lottery", "红包": "redpacket", "任务": "task"}
            kwargs = {f"{_key_map.get(name, name)}_stats": stats}
            self._send_notification(site_info.get("name", "躺平PT"), **kwargs)
        logger.info(f"[{name}] 定时任务完成")

    # ==================== API 接口 ====================

    def get_api(self) -> List[Dict[str, Any]]:
        """注册插件 API"""
        return [{
            "path": "/claim_task",
            "endpoint": self.claim_task_api,
            "methods": ["POST"],
            "auth": "bear",
            "summary": "自动领取躺平PT任务",
            "description": "获取任务列表并按照优先级规则自动领取当日任务",
        }]

    def claim_task_api(self) -> dict:
        """API: 手动触发任务领取"""
        site_info = self._get_site_info()
        if not site_info:
            return {"success": False, "message": "未找到躺平PT站点"}

        try:
            task_stats = self._run_task_claim(site_info)
            if task_stats.get("already_claimed_today"):
                return {
                    "success": True,
                    "message": f"今日已领取过任务「{task_stats.get('claimed_task')}」",
                    "data": task_stats,
                }
            if task_stats.get("claimed"):
                return {
                    "success": True,
                    "message": f"成功领取任务「{task_stats.get('claimed_task')}」",
                    "data": task_stats,
                }
            return {
                "success": False,
                "message": task_stats.get("message") or "无可领取的任务",
                "data": task_stats,
            }
        except Exception as e:
            logger.error(f"[任务] API调用异常: {str(e)}")
            return {"success": False, "message": f"执行异常: {str(e)}"}

    # ==================== 配置表单 ====================

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """拼装插件配置页面"""
        return [
            {
                'component': 'VForm',
                'content': [
                    # 第一排：启用插件 + 执行通知
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '执行通知',
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    # 第二排：自动抽奖 | 抽奖周期 | 立即抽奖
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled_lottery',
                                            'label': '自动抽奖',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron_lottery',
                                            'label': '自动抽奖周期',
                                            'placeholder': '留空则不定时执行',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce_lottery',
                                            'label': '立即抽奖',
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    # 第三排：自动领任务 | 任务周期 | 立即领任务
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled_task',
                                            'label': '自动领任务',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron_task',
                                            'label': '领任务周期',
                                            'placeholder': '留空则不定时执行',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce_task',
                                            'label': '立即领任务',
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    # 第四排：自动领红包 | 红包周期 | 立即领红包
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled_redpacket',
                                            'label': '自动领红包',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron_redpacket',
                                            'label': '领红包周期',
                                            'placeholder': '留空则不定时执行',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce_redpacket',
                                            'label': '立即领红包',
                                        }
                                    }
                                ]
                            },
                        ]
                    },

                    # 说明
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'density': 'compact',
                                            'text': '自动匹配 tangpt.top 站点（需先在站点管理中配置）。'
                                                    '抽奖默认每6h执行一次（0 */6 * * *），红包默认每10min执行一次（*/10 * * * *），'
                                                    '任务默认每6h执行一次（0 */6 * * *），留空则不定时执行。'
                                                    '抽奖固定循环至次数用完（间隔5s）；红包循环领取至无红包；'
                                                    '任务距今日结束≥2h可领，月末 BUG>VIP>苍蝇腿，平时只领苍蝇腿。'
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "enabled_lottery": False,
            "enabled_redpacket": False,
            "enabled_task": False,
            "cron_lottery": "0 */6 * * *",
            "cron_task": "0 */6 * * *",
            "cron_redpacket": "*/10 * * * *",
            "onlyonce_lottery": False,
            "onlyonce_redpacket": False,
            "onlyonce_task": False,
        }

    # ==================== 统计详情页面 ====================

    def get_page(self) -> List[dict]:
        """拼装插件详情页面，展示当日统计数据（现代化全宽设计）"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        redpacket_data = self.get_data(f"redpacket_stats_{date_str}") or {}
        lottery_data = self.get_data(f"lottery_stats_{date_str}") or {}
        task_data = self.get_data(f"task_stats_{date_str}") or {}

        if not any([redpacket_data, lottery_data, task_data]):
            return [{
                'component': 'VAlert',
                'props': {
                    'type': 'info', 'variant': 'tonal',
                    'text': f'今日（{date_str}）暂无统计数据，等待定时任务执行',
                    'class': 'mt-4',
                }
            }]

        page = [
            # 页面标题
            self._page_title(f'📊 今日统计（{date_str}）'),
        ]

        # ── 红包模块 ──
        if redpacket_data:
            rp = redpacket_data
            limit_reached = rp.get('daily_limit_reached') or (rp.get('daily_max', 0) > 0 and rp.get('claimed_count', 0) >= rp.get('daily_max', 0)) or rp.get('claimed_count', 0) >= 100
            limit_text = '已达上限' if limit_reached else '正常'
            limit_color = '#ef4444' if limit_reached else '#22c55e'

            page.append(self._section_header('🧧', '红包领取', '#f97316'))
            page.append(self._metric_grid([
                {'label': '领取个数', 'value': str(rp.get('claimed_count', 0)), 'color': '#f97316'},
                {'label': '获得魔力', 'value': self._fmt_num(rp.get('total_magic', 0)), 'color': '#22c55e'},
                {'label': '上限状态', 'value': limit_text, 'color': limit_color},
                {'label': '最后执行', 'value': rp.get('last_run', '-'), 'color': '#94a3b8'},
            ]))

        # ── 任务模块 ──
        if task_data:
            ts = task_data
            if ts.get('claimed'):
                status_text = '✅ 已领取'
                status_color = '#22c55e'
            else:
                status_text = '⚠️ 未成功'
                status_color = '#f59e0b'

            page.append(self._section_header('📋', '任务领取', '#06b6d4'))
            page.append(self._metric_grid([
                {'label': '任务名称', 'value': ts.get('claimed_task', '-'), 'color': '#06b6d4'},
                {'label': '领取状态', 'value': status_text, 'color': status_color},
                {'label': '返回消息', 'value': ts.get('message', '-'), 'color': '#94a3b8'},
                {'label': '领取时间', 'value': ts.get('claim_time', '-'), 'color': '#94a3b8'},
            ]))

        # ── 抽奖模块 ──
        if lottery_data:
            ls = lottery_data
            nc = ls.get('net_change', 0)
            nc_color = '#22c55e' if nc >= 0 else '#ef4444'

            page.append(self._section_header('🎰', '抽奖统计', '#8b5cf6'))
            page.append(self._metric_grid([
                {'label': '请求次数', 'value': str(ls.get('request_count', 0)), 'color': '#8b5cf6'},
                {'label': '累计抽奖', 'value': self._fmt_num(ls.get('total_draws', 0)), 'unit': '次',
                 'color': '#f59e0b'},
                {'label': '消耗魔力', 'value': self._fmt_num(ls.get('total_cost', 0)), 'color': '#ef4444'},
                {'label': '获得魔力', 'value': self._fmt_num(ls.get('total_awarded', 0)), 'color': '#22c55e'},
                {'label': '补偿魔力', 'value': self._fmt_num(ls.get('total_compensated', 0)), 'color': '#a78bfa'},
                {'label': '净变化', 'value': f"{'+' if nc > 0 else ''}{self._fmt_num(nc)}", 'color': nc_color},
                {'label': '开始余额', 'value': self._fmt_num(ls.get('first_bonus_after')), 'color': '#94a3b8'},
                {'label': '最终余额', 'value': self._fmt_num(ls.get('last_bonus_after')), 'color': '#f59e0b'},
            ]))

            # 执行时间
            start = ls.get('start_time', '-')
            end = ls.get('end_time', '-')
            page.append({
                'component': 'VRow',
                'content': [{
                    'component': 'VCol',
                    'props': {'cols': 12},
                    'content': [{
                        'component': 'VChip',
                        'props': {'color': 'grey-lighten-3', 'size': 'small', 'variant': 'flat',
                                  'prepend-icon': 'mdi-clock-outline'},
                        'text': f'{start} ~ {end}'
                    }]
                }]
            })

            # 权益明细
            summary = ls.get('summary', {})
            if summary:
                preferred = ['魔力值', '谢谢惠顾', '上传量', '补签卡', '邀请', '邀请已发放', '彩虹ID']
                keys = sorted(summary.keys(),
                              key=lambda k: (preferred.index(k) if k in preferred else len(preferred), k))
                chips = []
                for key in keys:
                    item = summary[key]
                    val = item.get('value', 0) if isinstance(item, dict) else item
                    unit = item.get('unit', '') if isinstance(item, dict) else ''
                    if val != 0 or key in preferred:
                        chips.append({
                            'component': 'VChip',
                            'props': {'color': 'grey-lighten-4', 'size': 'small', 'variant': 'flat',
                                      'class': 'mr-2 mb-1'},
                            'text': f'{key}: {self._fmt_num(val)} {unit}'
                        })
                if chips:
                    page.append({
                        'component': 'VRow',
                        'content': [{
                            'component': 'VCol', 'props': {'cols': 12},
                            'content': [{
                                'component': 'div',
                                'props': {'class': 'text-caption text-grey mb-1'},
                                'text': '🎁 权益明细'
                            }] + chips
                        }]
                    })

        return page

    # ── 页面组件辅助方法 ──

    @staticmethod
    def _page_title(text: str) -> dict:
        return {
            'component': 'VRow',
            'content': [{
                'component': 'VCol', 'props': {'cols': 12},
                'content': [{
                    'component': 'h3',
                    'props': {'class': 'text-h5 font-weight-bold mb-4'},
                    'text': text
                }]
            }]
        }

    @staticmethod
    def _section_header(icon: str, title: str, color: str) -> dict:
        return {
            'component': 'VRow',
            'content': [{
                'component': 'VCol', 'props': {'cols': 12},
                'content': [{
                    'component': 'div',
                    'props': {'class': 'd-flex align-center mb-2 mt-2'},
                    'content': [
                        {'component': 'span', 'props': {'class': 'text-h6 mr-2'}, 'text': icon},
                        {'component': 'span', 'props': {'class': 'text-h6 font-weight-bold', 'style': f'color: {color}'}, 'text': title},
                    ]
                }]
            }]
        }

    @staticmethod
    def _metric_grid(metrics: list) -> dict:
        """生成响应式指标网格，每行 4 个（移动端 2 个）"""
        cols = []
        for m in metrics:
            cols.append({
                'component': 'VCol',
                'props': {'cols': 6, 'sm': 3},
                'content': [{
                    'component': 'VCard',
                    'props': {'variant': 'flat', 'class': 'mb-2', 'color': '#f8fafc'},
                    'content': [{
                        'component': 'VCardText',
                        'props': {'class': 'pa-3 text-center'},
                        'content': [
                            {
                                'component': 'div',
                                'props': {'class': 'text-caption text-grey text-uppercase mb-1'},
                                'text': m.get('label', '')
                            },
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'text-h6 font-weight-bold',
                                    'style': f"color: {m.get('color', '#0f172a')}"
                                },
                                'text': m.get('value', '-')
                            },
                        ]
                    }]
                }]
            })
        return {
            'component': 'VRow',
            'content': cols,
        }

    @staticmethod
    def _fmt_num(val) -> str:
        """格式化数字：超过千位使用 K/M/B，否则直接展示"""
        if val is None:
            return '-'
        try:
            val = float(val)
        except (TypeError, ValueError):
            return str(val)
        abs_val = abs(val)
        if abs_val >= 1e9:
            return f"{val / 1e9:.2f}B"
        if abs_val >= 1e6:
            return f"{val / 1e6:.2f}M"
        if abs_val >= 1e3:
            return f"{val / 1e3:.2f}K"
        if val == int(val):
            return f"{int(val):,}"
        return f"{val:,.1f}"
