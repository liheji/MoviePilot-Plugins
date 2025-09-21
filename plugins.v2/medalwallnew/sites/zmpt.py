from typing import Dict, List
from datetime import datetime, timedelta
import pytz
from app.log import logger
from app.core.config import settings
from app.plugins.medalwallnew.sites import _IMedalSiteHandler


class ZmptMedalHandler(_IMedalSiteHandler):
    """织梦站点勋章处理器"""

    site_url = "zmpt.cc"

    @classmethod
    def match(cls, url: str) -> bool:
        """
        根据站点Url判断是否匹配当前站点勋章类，大部分情况使用默认实现即可
        :param url: 站点Url
        :return: 是否匹配，如匹配则会调用该类的fetch_medals方法
        """
        from app.utils.string import StringUtils
        if StringUtils.url_equal(url, cls.site_url):
            return True
        return False

    def fetch_medals(self, site_info) -> List[Dict]:
        """获取织梦站点勋章数据"""
        try:
            site_name = site_info.name
            site_url = site_info.url
            site_cookie = site_info.cookie

            # 发送请求获取勋章数据
            res = self._request_with_retry(
                url=f"{site_url}/javaapi/user/queryAllMedals",
                cookies=site_cookie
            )

            if not res:
                logger.error(f"请求勋章接口失败！站点：{site_name}")
                return []

            # 处理勋章数据
            data = res.json().get('result', {})
            medal_groups = data.get('medalGroups', [])
            medals = data.get('medals', [])

            # 用于去重的集合
            processed_medals = set()
            all_medals = []

            # 处理独立勋章
            for medal in medals:
                medal_data = self._process_medal(medal, site_name)
                medal_key = f"{medal_data['name']}_{site_name}"
                if medal_key not in processed_medals:
                    processed_medals.add(medal_key)
                    all_medals.append(medal_data)

            # 处理分组勋章
            for group in medal_groups:
                for medal in group.get('medalList', []):
                    medal_data = self._process_medal(medal, site_name)
                    medal_key = f"{medal_data['name']}_{site_name}"
                    if medal_key not in processed_medals:
                        processed_medals.add(medal_key)
                        all_medals.append(medal_data)

            return all_medals

        except Exception as e:
            logger.error(f"处理织梦站点勋章数据时发生错误: {str(e)}")
            return []

    def _process_medal(self, medal: Dict, site_name: str) -> Dict:
        """处理单个勋章数据"""
        try:
            has_medal = medal.get('hasMedal', False)
            image_small = medal.get('imageSmall', '')
            price = medal.get('price', 0)
            name = medal.get('name', '')
            sale_begin_time = medal.get('saleBeginTime', '')
            sale_end_time = medal.get('saleEndTime', '')
            bonus_rate = medal.get('bonusAdditionFactor', 0)

            # 确定购买状态
            if has_medal:
                purchase_status = '已经购买'
            elif self._is_current_time_in_range(sale_begin_time, sale_end_time):
                purchase_status = '购买'
            else:
                purchase_status = '未到可购买时间'

            # 格式化勋章数据
            return self.format_medal_data({
                'name': name,
                'imageSmall': image_small,
                'saleBeginTime': sale_begin_time,
                'saleEndTime': sale_end_time,
                'price': price,
                'site': site_name,
                'bonus_rate': '%.f%%' % (float(bonus_rate) * 100),
                'purchase_status': purchase_status,
            })

        except Exception as e:
            logger.error(f"处理勋章数据时发生错误: {str(e)}")
            return self.format_medal_data({
                'name': medal.get('name', '未知勋章'),
                'imageSmall': medal.get('imageSmall', ''),
                'site': site_name,
                'purchase_status': '未知状态',
            })

    def _is_current_time_in_range(self, start_time: str, end_time: str) -> bool:
        """判断当前时间是否在给定的时间范围内"""
        try:
            # 处理空值
            if not start_time or not end_time:
                return True

            # 处理"~"分隔符
            if "~" in start_time:
                start_time = start_time.split("~")[0].strip()
            if "~" in end_time:
                end_time = end_time.split("~")[1].strip()

            # 处理"不限"的情况
            if "不限" in start_time or "不限" in end_time:
                return True

            # 清理时间字符串
            start_time = start_time.strip()
            end_time = end_time.strip()

            # 处理空字符串
            if not start_time or not end_time:
                return True

            # 尝试解析时间
            try:
                # 使用系统时区
                current_time = datetime.now(pytz.timezone(settings.TZ))
                start_datetime = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=pytz.timezone(settings.TZ))
                end_datetime = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=pytz.timezone(settings.TZ))

                # 添加时间容差(5分钟)
                time_tolerance = timedelta(minutes=5)
                return (start_datetime - time_tolerance) <= current_time <= (end_datetime + time_tolerance)

            except ValueError as e:
                logger.warning(f"时间格式解析失败: {e}, start_time={start_time}, end_time={end_time}")
                return True

        except Exception as e:
            logger.error(f"解析时间范围时发生错误: {e}, start_time={start_time}, end_time={end_time}")
            return True

    def _request_with_retry(self, url: str, cookies: str = None, **kwargs):
        """带重试机制的请求方法"""
        from app.utils.http import RequestUtils

        req_kwargs = {
            'timeout': 30,
            'headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
        }

        # 设置 cookies
        if cookies:
            req_kwargs['cookies'] = cookies

        for i in range(3):
            try:
                res = RequestUtils(**req_kwargs).get_res(url=url, **kwargs)
                if res and res.status_code == 200:
                    return res
                if i < 2:
                    logger.warning(f"第{i + 1}次请求失败，5秒后重试...")
                    import time
                    time.sleep(5)
            except Exception as e:
                if i < 2:
                    logger.warning(f"第{i + 1}次请求异常：{str(e)}，5秒后重试...")
                    time.sleep(5)
                else:
                    raise e
        return None
