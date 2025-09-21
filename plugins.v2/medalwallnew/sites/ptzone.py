from typing import Dict, List
import pytz
from lxml import etree
from urllib.parse import parse_qs, urlparse
from app.log import logger
from app.core.config import settings
from app.plugins.medalwallnew.sites import _IMedalSiteHandler


class PtzoneMedalHandler(_IMedalSiteHandler):
    """织梦站点勋章处理器"""

    site_url = "ptzone.xyz"

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
        """获取PHP站点勋章数据"""
        try:
            site_name = site_info.name
            site_url = site_info.url
            site_cookie = site_info.cookie

            # 获取所有页面的勋章数据
            medals = []
            current_page = 0

            while True:
                # 构建分页URL
                url = f"{site_url.rstrip('/')}/medal.php"
                if current_page > 0:
                    url = f"{url}?page={current_page}"

                logger.info(f"正在获取第 {current_page + 1} 页勋章数据，URL: {url}")

                # 发送请求获取勋章页面
                res = self._request_with_retry(
                    url=url,
                    cookies=site_cookie
                )

                if not res:
                    logger.error(f"请求勋章页面失败！站点：{site_name}")
                    break

                # 使用lxml解析HTML
                html = etree.HTML(res.text)

                # 获取勋章表格
                medal_table = html.xpath("//table[@class='main']//table[contains(@border, '1')]")
                if not medal_table:
                    logger.error("未找到勋章表格！")
                    break

                # 处理当前页面的勋章数据
                for row in medal_table[0].xpath(".//tr")[1:]:  # 跳过表头
                    try:
                        medal = self._process_medal_row(row, site_name, site_url)
                        if medal:
                            medals.append(medal)
                    except Exception as e:
                        logger.error(f"处理行数据时发生错误：{str(e)}")
                        continue

                # 先查找"下一页"文字链接
                next_page = html.xpath("//p[@class='nexus-pagination']//a[contains(., '下一页')]")
                next_href = None
                if next_page:
                    next_href = next_page[0].get('href')
                    logger.info(f"通过'下一页'文字找到下一页链接: {next_href}")
                else:
                    # 没有"下一页"文字，遍历分页区所有子节点，找font标签后第一个a标签
                    pagination_nodes = html.xpath("//p[@class='nexus-pagination']/*")
                    found_current = False
                    for node in pagination_nodes:
                        tag = node.tag.lower()
                        if tag == 'font':
                            found_current = True
                        elif found_current and tag == 'a':
                            next_href = node.get('href')
                            logger.info(f"通过数字分页找到下一页链接: {next_href}")
                            break
                if not next_href:
                    logger.info("未找到下一页链接，已到达最后一页")
                    break
                # 解析URL参数
                try:
                    parsed = urlparse(next_href)
                    params = parse_qs(parsed.query)
                    next_page_num = int(params.get('page', [0])[0])
                    logger.info(f"解析到下一页页码: {next_page_num}")
                    if next_page_num <= current_page:
                        logger.info("下一页页码小于等于当前页码，已到达最后一页")
                        break  # 防止循环
                    current_page = next_page_num
                except (ValueError, IndexError, AttributeError) as e:
                    logger.error(f"解析页码时发生错误: {str(e)}")
                    break

            logger.info(f"共获取到 {len(medals)} 个勋章数据")
            return medals

        except Exception as e:
            logger.error(f"处理PHP站点勋章数据时发生错误: {str(e)}")
            return []

    def _process_medal_row(self, row, site_name: str, site_url: str) -> Dict:
        """处理单个勋章行数据"""
        cells = row.xpath(".//td")[1:]  # 适配 ptzone
        if len(cells) < 9:  # 确保有足够的列
            return None

        medal = {}

        # 图片
        img = cells[0].xpath(".//img/@src")
        if img:
            img_url = img[0]
            # 如果不是http/https开头，补全为完整站点URL
            if not img_url.startswith('http'):
                from urllib.parse import urljoin
                img_url = urljoin(site_url + '/', img_url.lstrip('/'))
            medal['imageSmall'] = img_url

        # 名称和描述
        name = ''
        description = ''
        h1_nodes = cells[1].xpath('./h1')
        if h1_nodes:
            name = h1_nodes[0].text.strip() if h1_nodes[0].text else ''
            description = h1_nodes[0].tail.strip() if h1_nodes[0].tail and h1_nodes[0].tail.strip() else ''
        else:
            description = ''.join(cells[1].xpath('.//text()')).strip()
        medal['name'] = name
        medal['description'] = description

        # 可购买时间
        time_text = cells[2].xpath(".//text()")
        if time_text:
            time_text = [t.strip() for t in time_text if t.strip()]
            if len(time_text) >= 2:
                medal['saleBeginTime'] = time_text[0]
                medal['saleEndTime'] = time_text[1]

        # 有效期
        validity = cells[3].xpath(".//text()")
        if validity:
            medal['validity'] = validity[0].strip()

        # 魔力加成
        bonus = cells[4].xpath(".//text()")
        if bonus:
            medal['bonus_rate'] = bonus[0].strip()

        # 价格
        price = cells[5].xpath(".//text()")
        if price:
            price_text = price[0].strip().replace(',', '')
            try:
                medal['price'] = int(price_text)
            except ValueError:
                medal['price'] = 0

        # 库存
        stock = cells[6].xpath(".//text()")
        if stock:
            medal['stock'] = stock[0].strip()

        # 购买状态
        buy_btn = cells[7].xpath(".//input/@value")
        if buy_btn:
            medal['purchase_status'] = buy_btn[0]

        # 赠送状态
        gift_btn = cells[8].xpath(".//input/@value")
        if gift_btn:
            medal['gift_status'] = gift_btn[0]

        # 站点信息
        medal['site'] = site_name

        return self.format_medal_data(medal)

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
