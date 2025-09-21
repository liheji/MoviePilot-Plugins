from typing import Dict, List
from app.log import logger
from lxml import etree
from app.plugins.medalwallnew.sites import _IMedalSiteHandler
from urllib.parse import urljoin


class QingwaMedalHandler(_IMedalSiteHandler):
    """青蛙站点勋章处理器"""

    site_url = "new.qingwa.pro"
    
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
        """获取青蛙站点勋章数据"""
        try:
            site_name = site_info.name
            site_url = site_info.url
            site_cookie = site_info.cookie
            
            # 获取勋章页面数据
            url = f"{site_url.rstrip('/')}/medal.php"
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
            
            # 获取所有勋章类型header
            medal_headers = html.xpath("//div[@class='medal-type-header']")
            if not medal_headers:
                logger.error("未找到勋章类型header！")
                return []
            
            logger.info(f"找到 {len(medal_headers)} 个勋章类型")
            
            # 处理所有勋章数据
            medals = []
            
            # 遍历所有勋章类型
            for header in medal_headers:
                try:
                    # 获取类型名称
                    type_name = header.xpath(".//span[@class='centered-text']/text()")
                    if type_name:
                        logger.info(f"处理勋章类型: {type_name[0]}")
                    
                    # 获取对应的container
                    container = header.xpath("following-sibling::div[@class='medal-type-container'][1]")
                    if not container:
                        continue
                        
                    # 获取该类型下的所有勋章项
                    medal_items = container[0].xpath(".//div[@class='medal-item']")
                    logger.info(f"找到 {len(medal_items)} 个勋章")
                    
                    for item in medal_items:
                        try:
                            medal = self._process_medal_item(item, site_name, site_url)
                            if medal:
                                medals.append(medal)
                        except Exception as e:
                            logger.error(f"处理勋章数据时发生错误：{str(e)}")
                            continue
                            
                except Exception as e:
                    logger.error(f"处理勋章类型时发生错误：{str(e)}")
                    continue
            
            logger.info(f"共获取到 {len(medals)} 个勋章数据")
            return medals
            
        except Exception as e:
            logger.error(f"处理青蛙站点勋章数据时发生错误: {str(e)}")
            return []

    def _process_medal_item(self, item, site_name: str, site_url: str) -> Dict:
        """处理单个勋章项数据"""
        medal = {}
        
        try:
            # 获取medal-info区域
            info_div = item.xpath(".//div[@class='medal-info']")[0]
            
            # 名称
            name = info_div.xpath(".//h2/text()")
            if name:
                medal['name'] = name[0].strip()
                
            # 图片 - 优先获取h2中的图片
            img = info_div.xpath(".//h2//img/@src")
            if not img:
                # 如果没有找到h2中的图片，则使用preview图片
                img = item.xpath(".//img[contains(@class, 'preview')]/@src")
                    
            if img:
                img_url = img[0]
                # 如果不是http/https开头，补全为完整站点URL
                if not img_url.startswith(('http://', 'https://')):
                    img_url = urljoin(site_url, img_url.lstrip('/'))
                medal['imageSmall'] = img_url
                
            # 描述
            description = info_div.xpath(".//p[contains(@style, 'display: flex')]/text()")
            if description:
                medal['description'] = description[0].strip()
                
            # 有效期
            validity = info_div.xpath(".//p[not(@style)]/text()")
            if validity:
                medal['validity'] = validity[0].strip()
                
            # 解析属性表格
            medal_details = info_div.xpath(".//table[@class='medal-details']//tr")
            for row in medal_details:
                cells = row.xpath(".//td/text()")
                if len(cells) == 2:
                    key, value = cells[0].strip(), cells[1].strip()
                    
                    if "蝌蚪加成" in key:
                        medal['bonus_rate'] = value
                    elif "加成有效期" in key:
                        medal['validity_period'] = value
                    elif "价格" in key:
                        try:
                            medal['price'] = int(value.replace(',', ''))
                        except ValueError:
                            medal['price'] = 0
                    elif "库存" in key:
                        medal['stock'] = value
                    elif "赠送手续费" in key:
                        medal['gift_fee'] = value
                        
            # 购买状态
            buy_btn = info_div.xpath(".//input[@type='button']/@value")
            if buy_btn:
                medal['purchase_status'] = buy_btn[0]
                
            # 站点信息
            medal['site'] = site_name
            
            return self.format_medal_data(medal)
            
        except Exception as e:
            logger.error(f"处理勋章项数据时发生错误: {str(e)}")
            return None

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
