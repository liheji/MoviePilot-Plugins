# -*- coding: utf-8 -*-
"""
tjupthelper 纯解析函数。

本模块不依赖 MoviePilot 宿主，只依赖标准库与 lxml，便于单元测试。
职责：
  - parse_forum_page    解析单个 tjupt 论坛主题页 -> 帖子字典列表
  - parse_reg_window    解析开注起止时间（支持中文/数字多种格式）
  - status_of           根据当前时间判定开注状态
"""
import html as htmlmod
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from lxml import etree

_BASE_URL = "https://www.tjupt.org"

# 用于把站点名与类别拆开的关键词
_CATEGORY_KEYWORDS = [
    "综合站", "综合", "影视", "动漫", "音乐", "游戏", "体育", "教育",
    "美剧", "日剧", "韩剧", "纪录片", "电影", "剧集", "资源", "PT",
    "综合类", "影视类", "动漫类", "音乐类", "游戏类", "非官", "官方",
]

# 开注时间字段的截断点
_REG_TAIL = (
    r"(?=站点地址|站外链接|站点简介|注册地址|站点名称及类别|站点名称|"
    r"网站地址|官网|链接|地址|开放注册时间|开注时间|$)"
)

# 开注时间标签（优先级排序，尽量兼容不同写法）
_REG_LABELS = [
    r"开放注册（邀请）时间",
    r"开放注册\(邀请\)时间",
    r"开放注册邀请时间",
    r"开放注册时间",
    r"开放\s*注册\s*时间",
    r"开注时间",
    r"开注（邀请）时间",
    r"开放时间",
    r"注册时间",
]

# 常见站点域名后缀（用于抓取裸域名）
_DOMAIN_SUFFIXES = r"(?:top|org|net|com|cc|me|xyz|vip|site|club|io|dev|fun|space|icu|pro)"


# --------------------------------------------------------------------------- #
# 文本清理
# --------------------------------------------------------------------------- #
def _strip_tags(raw: str) -> str:
    return re.sub(r"<[^>]+>", "", raw or "")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _unescape(text: str) -> str:
    return htmlmod.unescape(text or "")


def _plain_text(el) -> str:
    """取 lxml 元素纯文本，统一空白。"""
    text = etree.tostring(el, encoding="unicode", method="text")
    text = _unescape(text)
    return _collapse(text)


# --------------------------------------------------------------------------- #
# 日期解析
# --------------------------------------------------------------------------- #
def _make_dt(year, month, day, hh, mi, ss, default_hh=0, default_mi=0, default_ss=0):
    try:
        return datetime(
            int(year), int(month), int(day),
            int(hh) if hh is not None else default_hh,
            int(mi) if mi is not None else default_mi,
            int(ss) if ss is not None else default_ss,
        )
    except (ValueError, TypeError):
        return None


def _extract_date_tokens(text: str) -> List[Tuple]:
    """
    按出现顺序提取日期 token。
    每个 token: (year|None, month, day, hh|None, mi|None, ss|None)
    支持：
      2026 年 8 月 16 日
      2026年8月16日
      2026.8.16 / 2026/8/16 / 2026-8-16
      8 月 31 日（无年份，year 为 None，由调用方继承）
      08-31（无年份，数字格式）
      可选时间：00:00:00 / 00:00
    """
    pattern = re.compile(
        # 分支一：带年份的完整日期
        r"(?P<y>\d{4})\s*(?:年|[./-])\s*(?P<m>\d{1,2})\s*(?:月|[./-])\s*(?P<d>\d{1,2})\s*日?"
        r"(?:\s*(?P<hh>\d{1,2}):(?P<mi>\d{2})(?::(?P<ss>\d{2}))?)?"
        "|"
        # 分支二：仅月日（无年份），使用负向断言避免重复匹配到带年日期内部
        r"(?<![\d年])(?P<pm>\d{1,2})\s*月\s*(?P<pd>\d{1,2})\s*日?"
        r"(?:\s*(?P<phh>\d{1,2}):(?P<pmi>\d{2})(?::(?P<pss>\d{2}))?)?"
        "|"
        # 分支三：仅月日（数字），无年份
        r"(?<![\d./-])(?P<npm>\d{1,2})\s*[./-]\s*(?P<npd>\d{1,2})"
        r"(?:\s*(?P<nhh>\d{1,2}):(?P<nmi>\d{2})(?::(?P<nss>\d{2}))?)?"
    )
    tokens = []
    for m in pattern.finditer(text):
        if m.group("y"):
            tokens.append((
                int(m.group("y")), int(m.group("m")), int(m.group("d")),
                m.group("hh"), m.group("mi"), m.group("ss"),
            ))
        elif m.group("pm"):
            tokens.append((
                None, int(m.group("pm")), int(m.group("pd")),
                m.group("phh"), m.group("pmi"), m.group("pss"),
            ))
        elif m.group("npm") is not None:
            tokens.append((
                None, int(m.group("npm")), int(m.group("npd")),
                m.group("nhh"), m.group("nmi"), m.group("nss"),
            ))
    return tokens


def parse_reg_window(reg_text: str, default_year: Optional[int] = None) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    解析开注时间字符串，返回 (start_dt, end_dt)。
    例：
      "2026 年 8 月 29 日 00:00:00 到 2026 年 9 月 5 日 00:00:00"
      "2026年8月16日 — 8月31日"
      "2026.8.16 — 2026.8.31"
      "2026 年 08 月 27 日 00:00 至 2026 年 08 月 31 日 23:59"
    无时间时：开始默认 00:00:00，结束默认 23:59:59。
    """
    if not reg_text:
        return None, None
    tokens = _extract_date_tokens(reg_text)
    if not tokens:
        return None, None

    start_tok = tokens[0]
    end_tok = tokens[1] if len(tokens) > 1 else None

    start_year = start_tok[0]
    if start_year is None:
        start_year = default_year or datetime.now().year

    start_dt = _make_dt(
        start_year, start_tok[1], start_tok[2],
        start_tok[3], start_tok[4], start_tok[5],
        default_hh=0, default_mi=0, default_ss=0,
    )
    if start_dt is None:
        return None, None

    end_dt = None
    if end_tok:
        end_year = end_tok[0] or start_year
        end_dt = _make_dt(
            end_year, end_tok[1], end_tok[2],
            end_tok[3], end_tok[4], end_tok[5],
            default_hh=23, default_mi=59, default_ss=59,
        )
        # 跨年修正：结束日期早于开始日期且未显式给出年份时，结束年份 +1
        if end_dt is not None and end_dt < start_dt and end_tok[0] is None:
            end_dt = end_dt.replace(year=end_year + 1)

    return start_dt, end_dt


def status_of(reg_start: Optional[datetime], reg_end: Optional[datetime], now: Optional[datetime] = None) -> str:
    """判定开注状态：open / upcoming / closed / unknown。"""
    now = now or datetime.now()
    if not reg_start:
        return "unknown"
    if now < reg_start:
        return "upcoming"
    if reg_end is None:
        return "open"
    if now <= reg_end:
        return "open"
    return "closed"


def format_dt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


# --------------------------------------------------------------------------- #
# 字段提取
# --------------------------------------------------------------------------- #
def extract_site_name(text: str) -> Tuple[str, str]:
    """从正文提取 (站点名, 原始行)。"""
    m = re.search(
        r"站点名称及类别\s*[:：]\s*(.*?)(?=开放注册时间|开注时间|开放时间|站点地址|"
        r"站外链接|站点简介|注册地址|站点名称|$)",
        text,
    )
    raw = (m.group(1).strip() if m else "")
    return _clean_site_name(raw), raw


def _clean_site_name(raw: str) -> str:
    if not raw:
        return raw
    # 去掉末尾类别关键词（如 "蟹黄堡 综合" -> "蟹黄堡"）
    for kw in _CATEGORY_KEYWORDS:
        if raw.endswith(kw):
            name = raw[: -len(kw)].rstrip(" /-—：")
            if name:
                return name
    # 以空格分词，若最后一个词是类别关键词则去掉
    parts = raw.split()
    if len(parts) > 1 and parts[-1] in _CATEGORY_KEYWORDS:
        return " ".join(parts[:-1])
    # 去掉末尾的类别括号（如 "Animetorrents（动漫外站）" -> "Animetorrents"）
    m = re.search(r"[（(]([^（）()]{1,12})[）)]\s*$", raw)
    if m and any(kw in m.group(1) for kw in _CATEGORY_KEYWORDS):
        return raw[:m.start()].strip()
    return raw


def extract_site_url(text: str) -> str:
    m = re.search(r"https?://[^\s\"'<>]+", text)
    if m:
        return m.group(0)
    # 裸域名兜底（如 Pt.novahd.top / pt.muxuege.org）
    m = re.search(r"(?i)\b[A-Za-z0-9][A-Za-z0-9.-]*" + _DOMAIN_SUFFIXES + r"\b", text)
    return m.group(0) if m else ""


def extract_reg_text(text: str) -> str:
    """提取开注时间原始串（截断到站点地址等后续字段）。"""
    for label in _REG_LABELS:
        m = re.search(label + r"\s*[:：]\s*(.*?)" + _REG_TAIL, text, re.DOTALL)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


# --------------------------------------------------------------------------- #
# 页面解析
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
    default_year: Optional[int] = None,
) -> List[Dict]:
    """
    解析一个 tjupt 论坛主题页，返回帖子字典列表（顺序与页面呈现一致：旧->新）。
    每帖包含：pid/post_no/post_url/username/user_en_name/post_time
             site_name/site_name_raw/site_url/content/reg_start/reg_end。
    """
    if not html:
        return []
    tree = etree.HTML(html)
    if tree is None:
        return []

    # 帖子表头：id="pidNNN" 且不包含 body
    header_tables = tree.xpath('//table[starts-with(@id, "pid") and not(contains(@id, "body"))]')
    posts = []
    for ht in header_tables:
        pid = (ht.get("id") or "").replace("pid", "")
        if not pid.isdigit():
            continue
        meta = _parse_header_meta(ht, base_url)
        if meta["pid"] and not meta["post_url"]:
            meta["post_url"] = (
                f"{base_url}/forums.php?action=viewtopic&topicid={topic_id}"
                f"&page=p{meta['pid']}#pid{meta['pid']}"
            )

        body = tree.xpath(f'//div[@id="pid{pid}body"]')
        content = _plain_text(body[0]) if body else ""

        site_name, site_name_raw = extract_site_name(content)
        site_url = extract_site_url(content)
        reg_text = extract_reg_text(content)
        reg_start, reg_end = parse_reg_window(reg_text, default_year=default_year)

        posts.append({
            **meta,
            "site_name": site_name,
            "site_name_raw": site_name_raw,
            "site_url": site_url,
            "content": content,
            "reg_text": reg_text,
            "reg_start": format_dt(reg_start) if reg_start else "",
            "reg_end": format_dt(reg_end) if reg_end else "",
        })

    return posts


def _coerce_dt(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S",
                "%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
