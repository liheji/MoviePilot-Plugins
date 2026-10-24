import math
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from lxml import etree


TASK_PATH = "/task.php"
BONUS_PATH = "/mybonus.php"
CLAIMED_WORDS = ("已经认领", "已认领", "已领取", "已完成")
OPEN_WORDS = ("领取", "认领", "申请", "开始")
METRIC_LABELS = (
    "上传增量", "下载增量", "做种积分增量", "发种增量",
    "平均做种时间", "魔力增量",
)
LEVEL_NAMES = (
    "Staff Leader", "Nexus Master", "Extreme User", "Ultimate User",
    "Veteran User", "Moderator", "Administrator", "Power User", "Elite User",
    "Crazy User", "Insane User", "Uploader", "Retiree", "Peasant", "User",
    "Sysop", "VIP", "总版主", "维护开发员", "发布员", "养老族", "管理员",
    "主管", "贵宾",
)


def text_of(node) -> str:
    return " ".join(t.strip() for t in node.itertext() if t and t.strip())


def _lines(node) -> List[str]:
    lines = []
    for value in node.itertext():
        value = " ".join(value.split())
        if value and (not lines or value != lines[-1]):
            lines.append(value)
    return lines


def _text_lines(node) -> str:
    return "\n".join(_lines(node))


def _number(value: str) -> Optional[float]:
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", value or "")
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def format_amount(value: str) -> str:
    number = _number(value)
    if number is None:
        return value or "-"
    units = ((1_000_000_000, "B"), (1_000_000, "M"), (10_000, "W"), (1_000, "K"))
    for size, suffix in units:
        if abs(number) >= size:
            return f"{number / size:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{number:g}"


def _parse_dt(value: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_site_user_level(html: str) -> str:
    if not html:
        return ""
    root = etree.HTML(html)
    if root is None:
        return ""
    for value in root.xpath(
            "//a[contains(@href,'userdetails.php')]/@class"
            " | //a[contains(@href,'userdetails.php')]/@className"):
        for css_class in value.split():
            if css_class.endswith("_Name"):
                level = css_class[:-5].replace("_", " ").strip()
                return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", level)
    return ""


def userdetails_links(html: str) -> List[Dict[str, str]]:
    """用户详情链接及其 class 属性，用于诊断等级解析失败原因。"""
    if not html:
        return []
    root = etree.HTML(html)
    if root is None:
        return []
    return [
        {"href": a.get("href") or "", "class": a.get("class") or a.get("className") or ""}
        for a in root.xpath("//a[contains(@href,'userdetails.php')]")
    ]


def user_profile_href(html: str) -> str:
    """当前用户的 userdetails 链接，信息栏带等级 class 的优先。"""
    links = userdetails_links(html)
    if not links:
        return ""
    for link in links:
        if any(css.endswith("_Name") for css in link["class"].split()):
            return link["href"]
    return links[0]["href"]


JOIN_DATE_LABELS = ("加入日期", "注册日期", "注册时间", "加入时间")


def parse_join_date(html: str) -> Optional[str]:
    """从用户详情页解析加入日期，输出格式与 _join_days 兼容。"""
    if not html:
        return None
    root = etree.HTML(html)
    if root is None:
        return None
    for row in root.xpath("//tr"):
        cells = row.xpath("./th|./td")
        if len(cells) < 2:
            continue
        label = text_of(cells[0])
        if not any(word in label for word in JOIN_DATE_LABELS):
            continue
        value = text_of(cells[1])
        date = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", value)
        if not date:
            continue
        year, month, day = (int(part) for part in date.groups())
        time_part = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", value)
        if time_part:
            return (
                f"{year:04d}-{month:02d}-{day:02d} "
                f"{int(time_part.group(1)):02d}:{time_part.group(2)}:"
                f"{time_part.group(3) or '00'}"
            )
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def parse_hourly_magic(html: str) -> Optional[float]:
    """Read the base seed bonus rate, excluding medal/official bonuses."""
    if not html:
        return None
    root = etree.HTML(html)
    if root is None:
        return None
    for table in root.xpath("//table"):
        rows = table.xpath(".//tr")
        if not rows:
            continue
        headers = [text_of(cell) for cell in rows[0].xpath("./th|./td")]
        if "奖励类型" not in headers or not any(h.startswith("合计") for h in headers):
            continue
        earned_index = next(
            (i for i, header in enumerate(headers) if header.startswith("获得")), None
        )
        if earned_index is None:
            continue
        for row in rows[1:]:
            cells = row.xpath("./th|./td")
            if len(cells) > earned_index and text_of(cells[0]) == "基本奖励":
                value = _number(text_of(cells[earned_index]))
                if value is not None:
                    return value

    page_text = " ".join(text_of(root).split())
    patterns = (
        r"你当前每小时能获取\s*([\d,.]+)",
        r"当前每小时预计\s*([\d,.]+)",
        r"当前时魔\s*[:：]?\s*([\d,.]+)",
        r"每小时(?:能)?(?:获得|获取)\s*[:：]?\s*([\d,.]+)\s*(?:个)?魔力",
    )
    for pattern in patterns:
        match = re.search(pattern, page_text)
        if match:
            return _number(match.group(1))
    return None


def _metric_values(node) -> Tuple[List[str], Dict[str, str]]:
    raw_lines = _lines(node)
    display = []
    metrics = {}
    for line in raw_lines:
        parts = re.split(
            r"(?=(?:" + "|".join(map(re.escape, METRIC_LABELS)) + r")\s*[:：])",
            line,
        )
        for part in parts:
            if part.strip():
                display.append(part.strip())
            match = re.match(r"\s*(.+?)\s*[:：]\s*(.+?)\s*$", part)
            if match:
                metrics[match.group(1).strip()] = match.group(2).strip()
    return display, metrics


def _metric_number(metrics: Dict[str, str], label: str) -> Optional[float]:
    return _number(metrics.get(label, ""))


def _level_requirement(target: str) -> str:
    """目标用户文本中的等级要求描述。"""
    text = target.split("注册天数范围", 1)[0]
    return text.replace("用户等级", "").strip(" :：,，\n")


def _days_requirement(min_days: Optional[int], max_days: Optional[int]) -> str:
    if min_days is None:
        return f"{max_days} 天以内"
    if max_days is None:
        return f"{min_days} 天以上"
    return f"{min_days}~{max_days} 天"


def _level_allowed(target: str, level: str) -> Optional[bool]:
    if not target or "所有用户" in target or "不限" in target:
        return True
    if not level:
        return None
    level_text = target.split("注册天数范围", 1)[0]
    allowed = set()
    for name in LEVEL_NAMES:
        pattern = rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])"
        if re.search(pattern, level_text, re.IGNORECASE):
            allowed.add(name.casefold())
    return level.casefold() in allowed if allowed else None


def _registration_condition(target: str) -> Tuple[Optional[int], Optional[int]]:
    match = re.search(r"注册天数范围\s*[:：]\s*(\d+)\s*[~～-]\s*(--|\d+)", target)
    if not match:
        return None, None
    return int(match.group(1)), (
        int(match.group(2)) if match.group(2).isdigit() else None
    )


def _claimants(value: str) -> Tuple[Optional[int], Optional[int], str]:
    match = re.search(r"(\d+)\s*/\s*(\d+|无限)", value or "")
    if not match:
        return None, None, value or "-"
    current = int(match.group(1))
    limit = int(match.group(2)) if match.group(2).isdigit() else None
    return current, limit, f"{current}/{match.group(2)}"


def _check(name: str, value: str, ok: Optional[bool]) -> Dict:
    return {"name": name, "value": value, "ok": ok}


def evaluate_task(task: Dict, hourly_magic: float, now: datetime,
                  user_level: str = "", join_days: Optional[int] = None) -> str:
    start = _parse_dt(task.get("start", ""))
    end = _parse_dt(task.get("end", ""))
    if not start or not end:
        time_state, time_ok = "时间未识别", None
    elif now < start:
        time_state, time_ok = "未开始", False
    elif now > end:
        time_state, time_ok = "已结束", False
    else:
        time_state, time_ok = "进行中", True

    level_ok = _level_allowed(task.get("target", ""), user_level)
    level_requirement = _level_requirement(task.get("target", ""))
    checks = [_check(
        "当前等级", user_level or "未获取", level_ok if user_level else None
    )]

    min_days, max_days = task.get("registered_days_min"), task.get("registered_days_max")
    if min_days is not None or max_days is not None:
        if join_days is None:
            days_ok = None
        else:
            days_ok = (min_days is None or join_days >= min_days) and (
                max_days is None or join_days <= max_days
            )
        checks.append(_check(
            "注册天数",
            f"{join_days}天" if join_days is not None else "未获取",
            days_ok,
        ))

    claimants = task.get("claimants") or "-"
    count, limit = task.get("claimed_count"), task.get("claim_limit")
    if count is not None and limit is None:
        capacity_ok = True
    elif count is not None and limit is not None:
        capacity_ok = count < limit
    else:
        capacity_ok = None
    checks.append(_check("认领人数", claimants, capacity_ok))

    minimum = task.get("min_hourly_magic")
    if minimum is not None:
        if not hourly_magic or hourly_magic <= 0:
            hourly_ok = None
        else:
            hourly_ok = hourly_magic + 1e-9 >= minimum
        checks.append(_check("最低时魔", f"{minimum:g}/h", hourly_ok))

    task["checks"] = checks
    if task.get("claimed"):
        task["reason"] = "该任务已被你领取"
        return "已领取"
    if time_ok is False:
        task["reason"] = (
            f"任务 {start} 开始，尚未开放" if now < start else f"任务已于 {end} 结束"
        )
        return time_state
    if level_ok is False:
        task["reason"] = f"要求 {level_requirement}，当前 {user_level or '未获取'}"
        return "等级不符"
    if level_ok is None:
        task["reason"] = (
            f"要求 {level_requirement or '不限等级'}，未获取到你的用户等级"
        )
        return "等级未获取"
    if "注册天数" in {item["name"] for item in checks}:
        days_check = next(item for item in checks if item["name"] == "注册天数")
        if days_check["ok"] is False:
            task["reason"] = (
                f"要求注册 {_days_requirement(min_days, max_days)}，"
                f"当前已注册 {join_days} 天"
            )
            return "注册天数不符"
        if days_check["ok"] is None:
            task["reason"] = (
                f"要求注册 {_days_requirement(min_days, max_days)}，"
                "未获取到你的注册时间"
            )
            return "注册天数未获取"
    if capacity_ok is False:
        task["reason"] = f"认领名额已满（{claimants}）"
        return "认领已满"
    if minimum is not None:
        hourly_check = next(item for item in checks if item["name"] == "最低时魔")
        if hourly_check["ok"] is False:
            task["reason"] = (
                f"要求基础时魔 ≥ {minimum:g}/h，当前 {hourly_magic:g}/h"
            )
            return "时魔不足"
        if hourly_check["ok"] is None:
            task["reason"] = f"要求基础时魔 ≥ {minimum:g}/h，未获取到基础时魔"
            return "时魔未获取"
    if not task.get("claimable"):
        task["reason"] = (
            f"任务页未提供可用的领取入口（{task.get('action_text') or '无操作按钮'}）"
        )
        return "不可领取"
    task["reason"] = "全部条件满足，点击前往任务页领取"
    return "去领取"


def task_state(task: Dict, hourly_magic: float, now: datetime,
               user_level: str = "", join_days: Optional[int] = None) -> str:
    return evaluate_task(task, hourly_magic, now, user_level, join_days)


def apply_site_lock(tasks: List[Dict]) -> List[Dict]:
    """A site that already claimed one task cannot claim any other task."""
    if any(task.get("claimed") for task in tasks):
        for task in tasks:
            if not task.get("claimed"):
                task["state"] = "本站已领取"
                task["reason"] = "本站已领取其他任务，每站同时只能进行一个任务"
                task.setdefault("checks", []).append(
                    _check("", "已有任务被领取", False)
                )
    return tasks


def _task_headers(values: List[str]) -> Dict[str, int]:
    aliases = {
        "名称": ("名称",),
        "指标": ("指标",),
        "开始时间": ("开始时间",),
        "结束时间": ("结束时间",),
        "目标用户": ("目标用户",),
        "完成奖励": ("完成奖励魔力", "完成奖励"),
        "失败扣除": ("失败扣除魔力", "失败扣除"),
        "认领人数": ("认领人数",),
        "描述": ("描述",),
        "领取": ("领取", "操作"),
    }
    result = {}
    for key, names in aliases.items():
        for index, value in enumerate(values):
            if value.startswith(names):
                result[key] = index
                break
    return result


def _cell(cells, headers: Dict[str, int], key: str):
    index = headers.get(key)
    return cells[index] if index is not None and index < len(cells) else None


def _task_from_row(row, headers: Dict[str, int], header_values: List[str],
                   now: datetime, hourly_magic: float, user_level: str,
                   join_days: Optional[int]) -> Optional[Dict]:
    cells = row.xpath("./th|./td")
    if len(cells) < max(headers.values()) + 1:
        return None

    name_cell = _cell(cells, headers, "名称")
    metric_cell = _cell(cells, headers, "指标")
    action_cell = _cell(cells, headers, "领取")
    if name_cell is None or metric_cell is None or action_cell is None:
        return None

    raw_metrics, metrics = _metric_values(metric_cell)
    seed_points = _metric_number(metrics, "做种积分增量")
    publish = _metric_number(metrics, "发种增量")
    upload = _metric_number(metrics, "上传增量")
    download = _metric_number(metrics, "下载增量")
    seeding_hours = _metric_number(metrics, "平均做种时间")

    start = text_of(_cell(cells, headers, "开始时间")) if _cell(cells, headers, "开始时间") is not None else ""
    end = text_of(_cell(cells, headers, "结束时间")) if _cell(cells, headers, "结束时间") is not None else ""
    start_dt, end_dt = _parse_dt(start), _parse_dt(end)
    duration_hours = None
    if start_dt and end_dt and end_dt > start_dt:
        duration_hours = (end_dt - start_dt).total_seconds() / 3600
    min_hourly = None
    if seed_points is not None and duration_hours:
        min_hourly = math.ceil(seed_points / duration_hours * 1000) / 1000

    target = text_of(_cell(cells, headers, "目标用户")) if _cell(cells, headers, "目标用户") is not None else ""
    registered_days_min, registered_days_max = _registration_condition(target)

    action = next(iter(action_cell.xpath(
        ".//button|.//input[@type='button']|.//a"
    )), None)
    action_text = (text_of(action) or action.get("value", "")) if action is not None else ""
    disabled = action is None or action.get("disabled") is not None or (
        "disabled" in (action.get("class") or "") if action is not None else False
    )
    claimed = any(word in action_text for word in CLAIMED_WORDS)
    claimable = not disabled and not claimed and any(
        word in action_text for word in OPEN_WORDS
    )

    categories = []
    if seed_points is not None or seeding_hours is not None:
        categories.append("做种积分")
    if publish is not None:
        categories.append("发种")
    if upload is not None or download is not None:
        categories.append("上传&下载")
    if not categories:
        categories.append("魔力/签到" if "魔力增量" in metrics else "其他")

    core_indexes = set(headers.values())
    extra_fields = {}
    for index, title in enumerate(header_values):
        if index not in core_indexes and title and index < len(cells):
            value = text_of(cells[index])
            if value:
                extra_fields[title] = value

    claimants_text = (
        text_of(_cell(cells, headers, "认领人数"))
        if _cell(cells, headers, "认领人数") is not None else ""
    )
    claimed_count, claim_limit, claimants = _claimants(claimants_text)
    reward_index = headers["完成奖励"]
    penalty_index = headers["失败扣除"]
    task = {
        "name": text_of(name_cell),
        "metrics": raw_metrics,
        "metric_values": metrics,
        "start": start,
        "end": end,
        "duration_hours": duration_hours,
        "target": target,
        "reward": text_of(cells[reward_index]),
        "reward_name": header_values[reward_index],
        "penalty": text_of(cells[penalty_index]),
        "penalty_name": header_values[penalty_index],
        "extra_fields": extra_fields,
        "claimants": claimants,
        "claimed_count": claimed_count,
        "claim_limit": claim_limit,
        "registered_days_min": registered_days_min,
        "registered_days_max": registered_days_max,
        "description": (
            _text_lines(_cell(cells, headers, "描述"))
            if _cell(cells, headers, "描述") is not None else ""
        ),
        "action_text": action_text,
        "claimed": claimed,
        "claimable": claimable,
        "categories": categories,
        "seed_points": seed_points,
        "publish": publish,
        "upload": upload,
        "download": download,
        "seeding_hours": seeding_hours,
        "min_hourly_magic": min_hourly,
    }
    task["state"] = evaluate_task(task, hourly_magic, now, user_level, join_days)
    return task


def parse_tasks(html: str, now: datetime, hourly_magic: float = 0,
                user_level: str = "", join_days: Optional[int] = None) -> List[Dict]:
    if not html:
        return []
    root = etree.HTML(html)
    if root is None:
        return []

    for table in root.xpath("//table"):
        rows = table.xpath(".//tr")
        if not rows:
            continue
        header_values = [text_of(cell) for cell in rows[0].xpath("./th|./td")]
        headers = _task_headers(header_values)
        needed = {"名称", "指标", "开始时间", "结束时间", "目标用户",
                  "完成奖励", "失败扣除", "认领人数", "领取"}
        if not needed.issubset(headers):
            continue
        tasks = []
        for row in rows[1:]:
            task = _task_from_row(
                row, headers, header_values, now, hourly_magic, user_level, join_days
            )
            if task:
                tasks.append(task)
        return apply_site_lock(tasks)
    return []
