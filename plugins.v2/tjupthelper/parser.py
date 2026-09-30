# -*- coding: utf-8 -*-
"""
tjupthelper 解析模块（纯函数，不依赖 MoviePilot 宿主，只依赖标准库与 lxml，便于单元测试）。

职责划分：
  - 本地：HTML 结构解析（帖子边界/楼层/作者/发帖时间/正文文本）、AI 返回值的归一化与
          合法性校验、开注状态计算、方案A「遇到已结束即停止」判定。
  - AI  ：从帖子正文里识别「站点名 / 站点地址 / 开注起止时间」，见 openai/__init__.py。
"""
import html as htmlmod
import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from lxml import etree

_BASE_URL = "https://www.tjupt.org"

# 允许的年份范围：早于 2000 或晚于「当前年 + 5」的日期视为模型幻觉，直接丢弃
_MIN_YEAR = 2000
_MAX_YEAR_OFFSET = 5

# 站点名 / 地址两端的噪声字符（引号、标点、括号等）
_TRIM_CHARS = " \t\r\n\u3000\"'“”‘’「」『』【】[]()（）<>《》,，。、;；:："

# 中文数字（含 0-24 的常见写法）
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUM_CHARS = "0-9零〇一二两三四五六七八九十"

# 数字时刻（HH:MM / HH:MM:SS）
_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
# 带年份的中文/数字日期
_DATE_RE = re.compile(r"(\d{4})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?")
# 缺年份的月日（9月25日 / 09-25 / 9.25）
_YEARLESS_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?")
# 是否显式写了年份
_HAS_YEAR_RE = re.compile(r"\d{4}")
# 中文时刻写法：下午 3 点 / 晚上 8 点 30 分 / 上午 9 点半 / 零点 / 24 点
_CLOCK_RE = re.compile(
    r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|夜晚)?\s*"
    r"([" + _NUM_CHARS + r"]{1,3})\s*[点时點]\s*"
    r"(?:([" + _NUM_CHARS + r"]{1,3})\s*分?|(半))?\s*"
    r"(?:([" + _NUM_CHARS + r"]{1,3})\s*秒)?"
)
# 视为「没有开注时间」的占位写法
_JUNK_VALUES = ("null", "none", "n/a", "unknown", "not sure")


# --------------------------------------------------------------------------- #
# 文本清理
# --------------------------------------------------------------------------- #
def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _unescape(text: str) -> str:
    return htmlmod.unescape(text or "")


def _plain_text(el) -> str:
    """取 lxml 元素纯文本，统一空白。"""
    text = etree.tostring(el, encoding="unicode", method="text")
    text = _unescape(text)
    return _collapse(text)


def format_dt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _cn_number(text) -> Optional[int]:
    """中文数字或阿拉伯数字 -> int（支持 0-24，如 九 / 十一 / 二十四）。"""
    if text is None:
        return None
    text = str(text).strip()
    if text.isdigit():
        return int(text)
    if not text or any(ch not in _CN_DIGITS for ch in text):
        return None
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGITS[head] if head else 1
        ones = _CN_DIGITS[tail] if tail else 0
        return tens * 10 + ones
    return _CN_DIGITS[text]


def _normalize_dt_text(text: str, default_year: Optional[int] = None) -> str:
    """
    把 AI 可能返回的中文日期 / 中文时刻 / 缺年份写法归一化成 `YYYY-MM-DD HH:MM:SS`。
    例：`2026年9月26日零点` -> `2026-09-26 00:00:00`；`9月25日`（default_year=2026）-> `2026-09-25`；
        `2026-09-26 下午3点半` -> `2026-09-26 15:30:00`。无法归一化时原样返回。
    """
    date_match = _DATE_RE.search(text or "")
    if date_match:
        year, month, day = (int(date_match.group(idx)) for idx in (1, 2, 3))
    else:
        # 缺年份：只有给了基准年份（发帖年份）才补全
        loose_match = _YEARLESS_DATE_RE.search(text or "")
        if not loose_match or not default_year:
            return text
        year, month, day = int(default_year), int(loose_match.group(1)), int(loose_match.group(2))
    clock = _CLOCK_RE.search(text)
    if not clock:
        return f"{year:04d}-{month:02d}-{day:02d}"

    prefix = clock.group(1) or ""
    hour = _cn_number(clock.group(2))
    minute = _cn_number(clock.group(3))
    if clock.group(4):
        minute = 30
    second = _cn_number(clock.group(5))
    if hour is None:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if prefix in ("下午", "傍晚", "晚上", "夜里", "夜晚") and hour < 12:
        hour += 12
    elif prefix == "中午":
        hour = 12
    # 「24 点」等于次日零点
    roll_over = hour >= 24
    if roll_over:
        hour -= 24
    try:
        value = datetime(year, month, day, hour, minute or 0, second or 0)
    except ValueError:
        return text
    if roll_over:
        value += timedelta(days=1)
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _coerce_dt(value, end_of_day: bool = False, default_year: Optional[int] = None) -> Optional[datetime]:
    """
    把 AI 返回的时间字符串转成 datetime。
    :param end_of_day: 只给了日期、没给时刻时，是否补到当天 23:59:59（结束时间用）
    :param default_year: 缺年份时的基准年份（取该帖发帖年份）
    """
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    raw = str(value).strip()
    if not raw or raw.lower() in _JUNK_VALUES:
        return None

    has_time = bool(_TIME_RE.search(raw)) or bool(_CLOCK_RE.search(raw))
    for text in (raw, _normalize_dt_text(raw, default_year)):
        text = _collapse(text).replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
                    "%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日 %H:%M", "%Y年%m月%d日"):
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            if end_of_day and not has_time:
                parsed = parsed.replace(hour=23, minute=59, second=59)
            return parsed
    return None


def _sane(dt: Optional[datetime], now: datetime) -> Optional[datetime]:
    """过滤明显不可能的年份（模型幻觉）。"""
    if not dt:
        return None
    if dt.year < _MIN_YEAR or dt.year > now.year + _MAX_YEAR_OFFSET:
        return None
    return dt


def _fix_year_drift(start: Optional[datetime],
                    end: Optional[datetime],
                    post_dt: Optional[datetime]) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    纠正模型「把已经过去的活动滚到明年」的错误。
    判据：开始日期的月日不早于发帖月日（本应和发帖同年），年份却被写成发帖年 +1
    （等于提前一年发布公告）；此时把起止年份都减 1。
    真正的跨年公告（如 12 月发次年 1 月的窗口，月日小于发帖月日）不受影响。
    """
    if not start or not post_dt or start.year != post_dt.year + 1:
        return start, end
    if (start.month, start.day) < (post_dt.month, post_dt.day):
        return start, end
    shifted = start.replace(year=start.year - 1)
    if end and end.year == post_dt.year + 1:
        try:
            end = end.replace(year=end.year - 1)
        except ValueError:
            pass
    return shifted, end


def _clean_name(raw) -> str:
    return str(raw or "").strip(_TRIM_CHARS).strip()


def _clean_url(raw) -> str:
    url = re.sub(r"\s+", "", str(raw or ""))
    return url.strip(_TRIM_CHARS)


# --------------------------------------------------------------------------- #
# 状态判定
# --------------------------------------------------------------------------- #
def status_of(
    reg_start: Optional[datetime],
    reg_end: Optional[datetime],
    now: Optional[datetime] = None,
    permanent: bool = False,
) -> str:
    """判定开注状态：open / upcoming / closed / unknown。"""
    if permanent:
        return "open"
    now = now or datetime.now()
    if reg_start and now < reg_start:
        return "upcoming"
    if reg_end and now > reg_end:
        return "closed"
    if reg_start or reg_end:
        return "open"
    return "unknown"


def window_text(record: Dict) -> str:
    """开注时间展示文案；「长期开放」单独说明，避免出现 `- ~ -`；结束时间为系统推断时加标注。"""
    start = record.get("reg_start") or "-"
    end = record.get("reg_end") or "-"
    if record.get("permanent") and start == "-" and end == "-":
        return "长期开放（不限时）"
    text = f"{start} ~ {end}"
    if record.get("end_inferred") and record.get("reg_end"):
        text += "（结束时间为系统按开始 +3 天推断）"
    return text


# --------------------------------------------------------------------------- #
# AI 交互（组装请求文本 / 解析返回）
# --------------------------------------------------------------------------- #
def build_batch_text(posts: List[Dict]) -> str:
    """
    把一页帖子正文拼成带 [[n]] 编号的请求文本，编号与页内顺序一一对应。
    编号后面附带发帖时间（元信息），供 AI 换算「即日起 / 今日起」这类相对说法。
    """
    chunks = []
    for idx, post in enumerate(posts or [], start=1):
        pid = post.get("pid") or ""
        post_time = post.get("post_time") or "未知"
        body = _collapse(post.get("content") or "") or "(空)"
        chunks.append(f"[[{idx}]] (pid {pid}, 发帖时间 {post_time})\n{body}")
    return "\n\n".join(chunks)


def parse_ai_response(raw) -> Tuple[bool, List[Dict], str]:
    """
    解析 AI 返回内容，返回 (是否成功, items 列表, 错误信息)。
    容忍 ```json 代码块包裹、前后多余解释文字。
    """
    data = raw
    if isinstance(data, str):
        text = data.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[A-Za-z]*\s*", "", text)
            text = re.sub(r"\s*```\s*$", "", text).strip()
        if not text.startswith("{"):
            begin, end = text.find("{"), text.rfind("}")
            if begin != -1 and end > begin:
                text = text[begin:end + 1]
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return False, [], "AI 返回内容不是合法 JSON"

    if not isinstance(data, dict):
        return False, [], "AI 返回内容不是 JSON 对象"
    items = data.get("items")
    if items is None:
        return False, [], "AI 返回结果缺少 items 字段"
    if not isinstance(items, list):
        return False, [], "AI 返回的 items 不是数组"
    return True, [item for item in items if isinstance(item, dict)], ""


def apply_ai_items(
    posts: List[Dict],
    items: List[Dict],
    now: Optional[datetime] = None,
) -> List[Dict]:
    """
    把 AI 识别结果按 id 合并进帖子列表，并做归一化与合法性校验。
    返回新的帖子列表（不修改入参）。
    """
    now = now or datetime.now()
    by_id: Dict[int, Dict] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            by_id[int(item.get("id"))] = item
        except (TypeError, ValueError):
            continue

    result = []
    for idx, post in enumerate(posts or [], start=1):
        item = by_id.get(idx) or {}
        # 缺年份时以该帖发帖年份为基准（不能因为活动已过去就把年份滚到明年）
        post_dt = _coerce_dt(post.get("post_time"))
        post_year = post_dt.year if post_dt else None
        end_raw = str(item.get("reg_end") or "")

        start = _sane(_coerce_dt(item.get("reg_start"), default_year=post_year), now)
        end = _sane(_coerce_dt(item.get("reg_end"), end_of_day=True, default_year=post_year), now)
        start, end = _fix_year_drift(start, end, post_dt)
        if start and end and start > end:
            if _HAS_YEAR_RE.search(end_raw):
                # 两侧都写了年份却起止颠倒 -> 识别有误，整段作废更安全
                start = end = None
            else:
                # 结束日期缺年份且早于开始 -> 跨年（如「12月30日 - 1月5日」）
                try:
                    end = end.replace(year=end.year + 1)
                except ValueError:
                    start = end = None
        # 禁止没有结束时间：只给了开始时间时，按开始 +3 天的 23:59:59 兜底
        end_inferred = False
        if start and not end:
            end = (start + timedelta(days=3)).replace(hour=23, minute=59, second=59)
            end_inferred = True
        raw_name = str(item.get("site_name") or "").strip()
        record = dict(post)
        record.update({
            "site_name": _clean_name(raw_name),
            "site_name_raw": raw_name,
            "site_url": _clean_url(item.get("site_url")),
            "reg_start": format_dt(start),
            "reg_end": format_dt(end),
            "end_inferred": end_inferred,
            "permanent": bool(item.get("permanent")),
            "ai_note": str(item.get("note") or "").strip(),
            "ai_confidence": item.get("confidence"),
        })
        result.append(record)
    return result


# 临时的一次性「重扫修正」功能（用户要求后续删除）：最多往前扫描的页数
RESCAN_MAX_PAGES = 3


def scan_page_limit(max_pages: int, rescan: bool = False) -> int:
    """
    本次允许扫描的最大页数：正常扫描按用户配置；
    一次性「重扫修正」最多往前 RESCAN_MAX_PAGES 页，不做整帖回扫。
    """
    limit = max(1, int(max_pages or 1))
    if rescan:
        return max(1, min(limit, RESCAN_MAX_PAGES))
    return limit


def scan_page(posts: List[Dict],
              now: Optional[datetime] = None,
              stop_at_closed: bool = True) -> Tuple[List[Dict], List[Dict], bool]:
    """
    页面内按「最新优先」收集（页面内帖子由旧到新排列，故倒序遍历）。
    返回 (当前开注/即将开注的帖子, 开注已结束的帖子, 是否因遇到已结束而停止)。
    方案A：stop_at_closed=True 时遇到开注窗口已结束的帖子即停止（更旧的页面无需再扫）；
    重扫修正（stop_at_closed=False）时不停止，把已结束的帖子也交出来用于修正历史记录
    （页数上限由调用方按 scan_page_limit 控制）。
    """
    now = now or datetime.now()
    active: List[Dict] = []
    closed_posts: List[Dict] = []
    for post in reversed(posts or []):
        end = _coerce_dt(post.get("reg_end"))
        if end and end < now:
            closed_posts.append(post)
            if stop_at_closed:
                return active, closed_posts, True
            continue
        if post.get("reg_start") or post.get("reg_end") or post.get("permanent"):
            active.append(post)
    return active, closed_posts, False


def _record_changed(old: Dict, new: Dict) -> bool:
    """两份记录的关键字段是否有差异（用于日志提示「已修正历史记录」）。"""
    for key in ("site_name", "site_url", "reg_start", "reg_end", "permanent"):
        if (old.get(key) or "") != (new.get(key) or ""):
            return True
    return False


def merge_records(existing: List[Dict],
                  active: Dict[str, Dict],
                  closed: Dict[str, Dict]) -> Tuple[List[Dict], List[str]]:
    """
    合并入库：
      - active：当前开注/即将开注的帖子，新增或覆盖；
      - closed：开注已结束的帖子，只在库中已存在时覆盖（修正历史识别错误），不新增。
    返回 (按发帖时间倒序的记录列表, 被修正的 pid 列表)。
    """
    merged: Dict[str, Dict] = {}
    for record in existing or []:
        pid = record.get("pid")
        if pid:
            merged[pid] = dict(record)

    corrected: List[str] = []
    # 已结束的帖子：只在库里已有该 pid 时覆盖（修正历史识别错误），不新增
    for pid, record in (closed or {}).items():
        old = merged.get(pid)
        if not old:
            continue
        item = dict(record)
        item["first_seen"] = old.get("first_seen")
        if _record_changed(old, item):
            corrected.append(pid)
        merged[pid] = item
    # 当前开注/即将开注的帖子：新增或覆盖
    for pid, record in (active or {}).items():
        old = merged.get(pid)
        item = dict(record)
        item["first_seen"] = old.get("first_seen") if old else item.get("first_seen")
        if old and _record_changed(old, item):
            corrected.append(pid)
        merged[pid] = item

    records = sorted(merged.values(), key=lambda r: r.get("post_time") or "", reverse=True)
    return records, corrected


# --------------------------------------------------------------------------- #
# 论坛页面结构解析
# --------------------------------------------------------------------------- #
def _parse_header_meta(header_table, base_url: str) -> Dict:
    """解析帖子表头，返回 pid/post_no/post_url/username/user_en_name/post_time。"""
    header_html = etree.tostring(header_table, encoding="unicode")
    pid = (header_table.get("id") or "").replace("pid", "")
    if not pid.isdigit():
        pid = ""

    # 站外链接 jump link（#NNN）与 post url
    post_url = ""
    im = re.search(r'href="([^"]*?)"[^>]*>\s*#(\d+)', header_html)
    if im:
        post_url = base_url + "/" + _unescape(im.group(1)).lstrip("/")
    elif pid:
        post_url = f"{base_url}/forums.php?action=viewtopic&topicid=0&page=p{pid}#pid{pid}"

    post_no = f"#{pid}" if pid else ""

    # 用户名
    username = ""
    um = re.search(r'<span style="text-decoration:\s*none;">([^<]+)</span>', header_html)
    if um:
        username = _unescape(um.group(1)).strip()

    # 英文名（括号内）
    user_en_name = ""
    em = re.search(r"\(\s*<span[^>]*>.*?<b>([^<]+)</b>", header_html, re.DOTALL)
    if em:
        user_en_name = _unescape(em.group(1)).strip()

    # 发帖时间
    post_time = ""
    tm = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", header_html)
    if tm:
        post_time = tm.group(0)

    return {
        "pid": pid,
        "post_no": post_no,
        "post_url": post_url,
        "username": username,
        "user_en_name": user_en_name,
        "post_time": post_time,
    }


def parse_forum_page(
    html: str,
    topic_id: str = "15461",
    base_url: str = _BASE_URL,
) -> List[Dict]:
    """
    解析一个 tjupt 论坛主题页，返回帖子字典列表（顺序与页面呈现一致：旧->新）。
    每帖包含：pid/post_no/post_url/username/user_en_name/post_time/content。
    站点名/地址/开注时间由 AI 从 content 中识别（见 apply_ai_items）。
    """
    if not html:
        return []
    tree = etree.HTML(html)
    if tree is None:
        return []

    # 帖子表头：id="pidNNN" 且不包含 body
    header_tables = tree.xpath('//table[starts-with(@id, "pid") and not(contains(@id, "body"))]')
    posts = []
    for header_table in header_tables:
        pid = (header_table.get("id") or "").replace("pid", "")
        if not pid.isdigit():
            continue
        meta = _parse_header_meta(header_table, base_url)
        if meta["pid"] and not meta["post_url"]:
            meta["post_url"] = (
                f"{base_url}/forums.php?action=viewtopic&topicid={topic_id}"
                f"&page=p{meta['pid']}#pid{meta['pid']}"
            )

        body = tree.xpath(f'//div[@id="pid{pid}body"]')
        content = _plain_text(body[0]) if body else ""

        posts.append({**meta, "content": content})

    return posts
