# -*- coding: utf-8 -*-
"""
OpenAI 兼容大模型调用封装（配置与实现参考 autosigninnew）。

本插件自己的函数：
  - extract_open_reg：从一页论坛帖子的正文里识别各帖的站点名 / 站点地址 / 开注起止时间。

提示词中带上了当前系统时间，用于在正文出现多个时间（发帖时间、编辑时间、他人时间）时锁定
本次开注窗口、并推算缺失年份；请求文本里每个帖子还附带发帖时间，
仅在正文出现「即日起 / 今日起」这类相对说法时用于推算 reg_start。
"""
import time
from datetime import datetime
from typing import Optional, Tuple

import httpx
from openai import OpenAI

from app.core.config import settings
from app.log import logger

# 单次识别失败后的「立即重试」次数（0 = 不重试）；再失败就跳过该页
_RETRY_TIMES = 1
# 立即重试前的等待秒数
_RETRY_INTERVAL_SEC = 3


SYSTEM_PROMPT = """你是「北洋园PT论坛开注公告」信息抽取引擎。用户会给你一页论坛帖子的正文，帖子按 [[1]] [[2]] 这样的编号分隔，
每个编号后面括号里是该帖的发帖时间（元信息）。
当前系统时间：{now}（{tz}）；它只用于理解「今天 / 明天 / 本周末」这类相对说法。
缺年份的日期一律以该帖【发帖时间的年份】为基准（见规则 4），不要用系统时间去改动年份。

任务：对每个编号的帖子，抽取「开放注册（开注）」信息。

规则：
1. 只抽取该帖自身开注/开邀字段（「开放注册时间：」「开放注册（邀请）时间：」「开放邀请时间：」「开注时间：」「注册时间：」等）里的时间；
   忽略最后编辑时间、引用或回复里他人的时间、站点成立时间、考核时间等一切与本次开注无关的日期。
2. 发帖时间只是元信息，不是开注时间；只有正文出现「即日起 / 今日起 / 自即日起 / 本日起」这类没有具体时刻的相对说法时，
   才用它作为 reg_start（取发帖当天 00:00:00），其余情况一律忽略发帖时间。
3. 只取开注标签之后、到下一个字段（站点地址 / 站外链接 / 站点名称及类别 / 站点简介 等）为止的那一段，
   不要把「站点地址」附近的日期、或上一个帖子的时间算进来；「站点简介」里的日期只在开注字段完全没给时间时才可参考。
4. 常见写法与对应输出（务必换算成 24 小时制的数字时刻，结果里禁止残留「零点 / 下午 / 晚上 / 点半」这类中文词）：
   - 「2026 年 8 月 29 日 00:00:00 到 2026 年 9 月 5 日 00:00:00」-> reg_start=2026-08-29 00:00:00, reg_end=2026-09-05 00:00:00
   - 「2026年8月16日 — 8月31日」-> reg_start=2026-08-16 00:00:00, reg_end=2026-08-31 23:59:59（结束缺时刻补 23:59:59）
   - 「至9月26日零点」「到9月26日零点」「截止9月26日0点」：只有结束时间 -> reg_end=2026-09-26 00:00:00，reg_start=null
   - 「即日起开放注册到9月26日零点」-> reg_start=发帖当天 00:00:00，reg_end=2026-09-26 00:00:00
   - 「零点 / 0点 / 0:00 / 凌晨0点」= 当天 00:00:00；「24点 / 24:00」= 次日 00:00:00
   - 「中午」= 12:00:00；「下午2点」= 14:00:00；「晚上8点半」= 20:30:00；「上午9点30分」= 09:30:00
   - 缺年份（只有「9月25日」「09-25」）：年份取该帖发帖时间的年份；只有结束日期的月日小于开始日期的月日（跨年，如「12月30日 - 1月5日」）时，结束年份才取发帖年份 +1
   - 绝对不要把已经过去的日期推到明年：发帖 2026-09-22、正文写「9月25日-9月27日」就是 2026-09-25 ~ 2026-09-27；即使活动已经结束也要照实输出，由系统去判定「开注过期」
   - 只有开始时间：reg_end 为 null；缺时分秒：开始补 00:00:00，结束补 23:59:59
   - 结束时间缺失（正文只给开始时间，或写「待定 / 另行通知 / 结束时间未定」）：reg_end 照实给 null，
     禁止自己编造结束日期，系统会按 reg_start +3 天的 23:59:59 兜底
   - 写明「长期开放 / 不限时 / 随时开放」：reg_start、reg_end 均为 null，permanent 为 true
5. 无法确定开注时间（not sure / Unknown / 纯闲聊 / 与开注无关）：reg_start、reg_end 均为 null，禁止编造。
6. site_name 去掉类别后缀（「蟹黄堡 综合」->「蟹黄堡」，「聆音Club 综合」->「聆音Club」，「Animetorrents（动漫外站）」->「Animetorrents」）；
   site_url 只取该帖自己给出的站点地址，没有则 null。
7. 严格只输出 JSON，不要解释，不要 markdown 代码块。

示例（当前系统时间 2026-09-20 10:00:00）：
输入：
[[1]] (pid 241500, 发帖时间 2026-09-20 09:29:41)
站点名称及类别：聆音Club 综合 开放注册时间：至9月26日零点 站点地址：站外链接 :: https://pt.soulvoice.club/ 站点简介： 引用 建站七周年了，即日起开放注册到9月26日零点，同时全站free到26日零点
输出：
{{"items":[{{"id":1,"site_name":"聆音Club","site_url":"https://pt.soulvoice.club/","reg_start":"2026-09-20 00:00:00","reg_end":"2026-09-26 00:00:00","permanent":false,"confidence":0.95,"note":"开放注册时间：至9月26日零点"}}]}}

示例2（当前系统时间 2026-09-30 10:00:00；该活动已结束，仍按原年份输出，不要改成 2027）：
输入：
[[1]] (pid 241520, 发帖时间 2026-09-22 20:11:00)
站点名称及类别：DStudio 综合 开放邀请时间：9月25日-9月27日 站点地址：站外链接 :: https://dstudio.me/ 站点简介： 引用 2026.09.22 中秋国庆活动详情 暂定内容如下 9月25日-9月27日开放注册，并全站站免。 10月1日-10月3日调低邀请码魔力值。
输出：
{{"items":[{{"id":1,"site_name":"DStudio","site_url":"https://dstudio.me/","reg_start":"2026-09-25 00:00:00","reg_end":"2026-09-27 23:59:59","permanent":false,"confidence":0.95,"note":"开放邀请时间：9月25日-9月27日"}}]}}

输出格式（字段全部必填，没有的值用 null；时间一律 YYYY-MM-DD HH:MM:SS）：
{{"items":[{{"id":1,"site_name":"站点名","site_url":"https://example.com/","reg_start":"YYYY-MM-DD HH:MM:SS","reg_end":"YYYY-MM-DD HH:MM:SS","permanent":false,"confidence":0.9,"note":"原文中的开注字段"}}]}}
帖子若无开注信息，对应项 site_name、site_url、reg_start、reg_end 均为 null，permanent 为 false。"""


class OpenAi(object):
    """基于 OpenAI SDK 的大模型调用封装"""

    def __init__(self,
                 api_key: str = None,
                 api_url: str = None,
                 model: str = "gpt-4o",
                 proxy: dict = None,
                 timeout: float = 60.0):

        if not api_key or not api_url:
            raise ValueError("API key and API URL are required for OpenAi initialization.")

        self._api_key = api_key
        self._api_url = api_url
        self._model = model or "gpt-4o"

        # 配置代理客户端
        http_client = None
        if proxy and proxy.get("https"):
            http_client = httpx.Client(proxy=proxy.get("https"), timeout=timeout)

        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._api_url,
            http_client=http_client,
            timeout=timeout,
        )

    def _chat(self, message: str, prompt: str = None, user: str = "MoviePilot") -> str:
        """通用纯文本请求方法"""
        messages = []
        if prompt:
            messages.append({"role": "system", "content": prompt})
        messages.append({"role": "user", "content": message})

        completion = self._client.chat.completions.create(
            model=self._model,
            user=user,
            messages=messages,
        )
        return (completion.choices[0].message.content or "").strip()

    def extract_open_reg(self, page_text: str, now: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        识别一页帖子的开注信息。
        :param page_text: 带 [[n]] 编号的帖子正文（见 parser.build_batch_text）
        :param now: 当前系统时间，写入提示词用于消歧
        :return: (是否成功, AI 返回的 JSON 文本 / 错误信息)
        """
        now = now or datetime.now()
        prompt = SYSTEM_PROMPT.format(
            now=now.strftime("%Y-%m-%d %H:%M:%S"),
            tz=getattr(settings, "TZ", "Asia/Shanghai"),
        )

        last_err = ""
        for attempt in range(_RETRY_TIMES + 1):
            try:
                return True, self._chat(message=page_text, prompt=prompt)
            except Exception as e:
                last_err = str(e)
                logger.error(f"[北洋园PT开注助手] 大模型识别失败（第 {attempt + 1} 次）：{last_err}")
                if attempt < _RETRY_TIMES:
                    time.sleep(_RETRY_INTERVAL_SEC)
        return False, last_err
