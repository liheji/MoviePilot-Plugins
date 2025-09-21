from typing import Dict, List
from app.log import logger
from lxml import etree
from app.plugins.medalwallnew.sites import _IMedalSiteHandler
from urllib.parse import urljoin, parse_qs, urlparse


class HhanclubMedalHandler(_IMedalSiteHandler):
    """HHan站点勋章处理器"""

    site_url = "hhanclub.top"

    # 备用域名列表
    FALLBACK_DOMAINS = ['hhanclub.top']
    
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

    def _get_medals_page(self, url: str, cookies: str) -> str:
        """获取勋章页面内容，支持域名切换"""
        # 首先尝试原始URL
        res = self._request_with_retry(url=url, cookies=cookies)
        if res:
            return res.text
            
        # 如果原始URL失败，尝试备用域名
        parsed_url = urlparse(url)
        
        for fallback_domain in self.FALLBACK_DOMAINS:
            # 构建新的URL，保持path和query参数不变
            new_url = f"{parsed_url.scheme}://{fallback_domain}{parsed_url.path}"
            if parsed_url.query:
                new_url += f"?{parsed_url.query}"
                
            logger.info(f"尝试使用备用域名访问: {new_url}")
            res = self._request_with_retry(url=new_url, cookies=cookies)
            if res:
                return res.text
                
        return None

    def fetch_medals(self, site_info) -> List[Dict]:
        """获取HHan站点勋章数据"""
        try:
            site_name = site_info.name
            site_url = site_info.url
            site_cookie = site_info.cookie
            
            # 获取所有页面的勋章数据
            medals = []
            current_page = 0  # 从第0页开始
            
            while True:
                # 构建分页URL
                url = f"{site_url.rstrip('/')}/medal.php"
                if current_page > 0:
                    url = f"{url}?page={current_page}"
                
                logger.info(f"正在获取第 {current_page + 1} 页勋章数据，URL: {url}")
                
                # 发送请求获取勋章页面，支持域名切换
                page_content = self._get_medals_page(url, site_cookie)
                
                if not page_content:
                    logger.error(f"请求勋章页面失败！站点：{site_name}")
                    break
                    
                # 使用lxml解析HTML
                html = etree.HTML(page_content)
                
                # 获取勋章列表
                medal_items = html.xpath("//div[contains(@class, 'medal-table') and not(contains(@class, 'bg-[#4F5879]'))]")
                if not medal_items:
                    logger.error("未找到勋章列表！")
                    break
                
                # 处理当前页面的勋章数据
                for item in medal_items:
                    try:
                        medal = self._process_medal_item(item, site_name, site_url)
                        if medal:
                            medals.append(medal)
                    except Exception as e:
                        logger.error(f"处理勋章数据时发生错误：{str(e)}")
                        continue
                
                # 检查是否有下一页
                next_page = html.xpath("//a[contains(@class, 'bg-[#F29D38]') and not(@disabled)]")
                if not next_page:
                    logger.info("未找到下一页链接，已到达最后一页")
                    break
                
                logger.info("找到下一页链接，准备获取下一页数据")
                    
                # 从href中提取页码
                next_href = next_page[0].get('href')
                if not next_href:
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
            logger.error(f"处理HHan站点勋章数据时发生错误: {str(e)}")
            return []

    def _process_medal_item(self, item, site_name: str, site_url: str) -> Dict:
        """处理单个勋章数据"""
        medal = {}
        
        # 图片
        img = item.xpath(".//img/@src")
        if img:
            img_url = img[0]
            # 如果不是http/https开头，补全为完整站点URL
            if not img_url.startswith('http'):
                img_url = urljoin(site_url, img_url.lstrip('/'))
            medal['imageSmall'] = img_url
            
        # 名称和描述
        name_div = item.xpath(".//div[contains(@class, 'text-[18px]')]")
        if name_div:
            medal['name'] = name_div[0].text.strip()
            
        desc_div = item.xpath(".//div[contains(@class, 'text-[#9B9B9B]')]")
        if desc_div:
            medal['description'] = desc_div[0].text.strip()
            
        # 价格
        price_div = item.xpath(".//div[contains(text(), ',')]")
        if price_div:
            price_text = price_div[0].text.strip().replace(',', '')
            try:
                medal['price'] = int(price_text)
            except ValueError:
                medal['price'] = 0
                
        # 库存
        stock_div = item.xpath(".//div[contains(text(), '无限') or contains(text(), '0') or contains(text(), '1') or contains(text(), '2') or contains(text(), '3') or contains(text(), '4') or contains(text(), '5') or contains(text(), '6') or contains(text(), '7') or contains(text(), '8') or contains(text(), '9')]")
        if stock_div:
            medal['stock'] = stock_div[0].text.strip()
            
        # 魔力加成
        bonus_div = item.xpath(".//div[contains(text(), '%')]")
        if bonus_div:
            medal['bonus_rate'] = bonus_div[0].text.strip()
            
        # 有效期
        validity_div = item.xpath(".//div[contains(text(), '永久有效') or contains(text(), '天')]")
        if validity_div:
            medal['validity'] = validity_div[0].text.strip()
            
        # 购买状态
        buy_btn = item.xpath(".//input[@type='button']/@value")
        if buy_btn:
            medal['purchase_status'] = buy_btn[0]
            
        # 赠送状态
        gift_btn = item.xpath(".//input[@type='button'][2]/@value")
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
                    logger.warning(f"第{i+1}次请求失败，5秒后重试...")
                    import time
                    time.sleep(5)
            except Exception as e:
                if i < 2:
                    logger.warning(f"第{i+1}次请求异常：{str(e)}，5秒后重试...")
                    time.sleep(5)
                else:
                    raise e
        return None
