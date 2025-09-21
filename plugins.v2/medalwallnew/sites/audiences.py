from typing import Dict, List
from app.log import logger
from lxml import etree
from app.plugins.medalwallnew.sites import _IMedalSiteHandler
from urllib.parse import urljoin


class AudiencesMedalHandler(_IMedalSiteHandler):
    """观众站点勋章处理器"""

    site_url = "audiences.me"
    
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
        """获取观众站点勋章数据"""
        try:
            site_name = site_info.name
            site_url = site_info.url
            site_cookie = site_info.cookie
            
            # 构建勋章页面URL
            url = f"{site_url.rstrip('/')}/medal_center.php"
            
            logger.info(f"正在获取勋章数据，URL: {url}")
            
            # 发送请求获取勋章页面
            res = self._request_with_retry(
                url=url,
                cookies=site_cookie
            )
            
            if not res:
                logger.error(f"请求勋章页面失败！站点：{site_name}")
                return []
                
            # 使用lxml解析HTML
            html = etree.HTML(res.text)
            
            # 获取所有勋章项
            medal_items = html.xpath("//form[contains(@action, '?')]")
            if not medal_items:
                logger.error("未找到勋章数据！")
                return []
            
            logger.info(f"找到 {len(medal_items)} 个勋章")
            
            # 处理勋章数据
            medals = []
            for item in medal_items:
                try:
                    medal = self._process_medal_item(item, site_name, site_url)
                    if medal:
                        medals.append(medal)
                except Exception as e:
                    logger.error(f"处理勋章数据时发生错误：{str(e)}")
                    continue
            
            logger.info(f"共获取到 {len(medals)} 个勋章数据")
            return medals
            
        except Exception as e:
            logger.error(f"处理Audiences站点勋章数据时发生错误: {str(e)}")
            return []

    def _process_medal_item(self, item, site_name: str, site_url: str) -> Dict:
        """处理单个勋章项数据"""
        medal = {}
        
        # 图片
        img = item.xpath(".//img/@src")
        if img:
            img_url = img[0]
            # 如果不是http/https开头，补全为完整站点URL
            if not img_url.startswith('http'):
                img_url = urljoin(site_url + '/', img_url.lstrip('/'))
            medal['imageSmall'] = img_url
            
        # 名称
        name = item.xpath(".//h1/text()")
        if name:
            medal['name'] = name[0].strip()
            
        # 描述
        description = item.xpath(".//td[@class='colfollow'][2]/text()")
        if description:
            medal['description'] = description[0].strip()
            
        # 价格
        price = item.xpath(".//td[@class='colfollow'][3]/text()")
        if price:
            try:
                medal['price'] = int(price[0].replace(',', ''))
            except ValueError:
                medal['price'] = 0
                
        # 库存
        stock = item.xpath(".//td[@class='colfollow'][4]/text()")
        if stock:
            medal['stock'] = stock[0].strip()
            
        # 限购
        limit = item.xpath(".//td[@class='colfollow'][5]/text()")
        if limit:
            medal['limit'] = limit[0].strip()
            
        # 爆米花加成百分比
        bonus_rate = item.xpath(".//td[@class='colfollow'][6]/text()")
        if bonus_rate:
            medal['bonus_rate'] = bonus_rate[0].strip()
            
        # 加成天数
        validity = item.xpath(".//td[@class='colfollow'][7]/text()")
        if validity:
            medal['validity'] = validity[0].strip()
            
        # 购买类型
        purchase_type = item.xpath(".//td[@class='colfollow'][8]/text()")
        if purchase_type:
            medal['purchase_type'] = purchase_type[0].strip()
            
        # 购买状态
        buy_btn = item.xpath(".//input[@type='submit']/@value")
        if buy_btn:
            medal['purchase_status'] = buy_btn[0]
            
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
