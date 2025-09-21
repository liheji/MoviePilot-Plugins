from typing import Dict, List
from app.log import logger
from lxml import etree
from app.plugins.medalwallnew.sites import _IMedalSiteHandler
from urllib.parse import parse_qs, urlparse
import time
import re
from datetime import datetime


class PtsbaoMedalHandler(_IMedalSiteHandler):
    """PTSBAO站点勋章处理器"""

    site_url = "ptsbao.club"
    
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
        """获取PTSBAO站点勋章数据，遍历form节点保证准确性"""
        try:
            site_name = site_info.name
            site_url = site_info.url
            site_cookie = site_info.cookie
            medals = []
            current_page = 0
            
            # 检查cookie
            if not site_cookie:
                logger.error(f"站点 {site_name} 的cookie为空！")
                return []
                
            while True:
                # 构建URL
                url = f"{site_url.rstrip('/')}/mymedal.php"
                if current_page > 0:
                    url = f"{url}?page={current_page}"
                logger.info(f"正在获取第 {current_page + 1} 页勋章数据，URL: {url}")
                
                # 发送请求
                res = self._request_with_retry(url=url, cookies=site_cookie)
                if not res:
                    logger.error(f"请求勋章页面失败！站点：{site_name}")
                    break
                    
                # 检查响应内容
                if not res.text:
                    logger.error(f"响应内容为空！站点：{site_name}")
                    break
                    
                # 记录响应内容长度
                logger.info(f"响应内容长度: {len(res.text)}")
                
                # 解析HTML
                html = etree.HTML(res.text)
                if html is None:
                    logger.error("HTML解析失败！")
                    break
                    
                # 获取勋章form
                medal_forms = html.xpath("//table[@align='center' and @width='90%']//form")
                if not medal_forms:
                    logger.error("未找到勋章form节点！")
                    # 尝试其他可能的选择器
                    medal_forms = html.xpath("//form[contains(@id, 'medalForm')]")
                    if not medal_forms:
                        logger.error("使用备用选择器也未找到勋章form节点！")
                        break
                    else:
                        logger.info(f"使用备用选择器找到 {len(medal_forms)} 个勋章form节点")
                
                logger.info(f"找到 {len(medal_forms)} 个勋章form节点")
                
                # 处理每个勋章
                for form in medal_forms:
                    try:
                        medal = self._process_medal_row(form, site_name, site_url)
                        if medal:
                            medals.append(medal)
                            logger.info(f"成功处理勋章: {medal.get('name', '未知')}")
                    except Exception as e:
                        logger.error(f"处理form勋章数据时发生错误：{str(e)}")
                        continue
                
                # 检查是否有下一页
                next_page = html.xpath("//a[contains(text(), '下一页')]")
                if not next_page:
                    logger.info("未找到下一页链接，已到达最后一页")
                    break
                    
                logger.info("找到下一页链接，准备获取下一页数据")
                next_href = next_page[0].get('href')
                if not next_href:
                    logger.error("下一页链接没有href属性")
                    break
                    
                # 解析下一页URL
                parsed = urlparse(next_href)
                params = parse_qs(parsed.query)
                try:
                    next_page_num = int(params.get('page', [0])[0])
                    if next_page_num <= current_page:
                        logger.info("下一页页码小于等于当前页码，已到达最后一页")
                        break
                    current_page = next_page_num
                except Exception as e:
                    logger.error(f"解析页码时发生错误: {str(e)}")
                    break
                    
                # 添加延时避免请求过快
                time.sleep(2)
                
            logger.info(f"共获取到 {len(medals)} 个勋章数据")
            return medals
            
        except Exception as e:
            logger.error(f"处理PTSBAO站点勋章数据时发生错误: {str(e)}")
            return []

    def _process_medal_row(self, form, site_name: str, site_url: str) -> Dict:
        """处理单个勋章form节点，详细注释每一步"""
        try:
            # 取出form下所有的td，不再要求tr
            tds = form.xpath(".//td")
            if len(tds) < 6:
                logger.error(f"勋章行数据不完整，td数量: {len(tds)}")
                # 尝试直接从form获取td
                tds = form.xpath("./td")
                if len(tds) < 6:
                    logger.error(f"直接获取td也失败，td数量: {len(tds)}")
                    return None
                else:
                    logger.info(f"成功直接获取到td，数量: {len(tds)}")
            
            medal = {}
            
            # 1. 图片，优先data-original，没有就解析style
            try:
                medal_container = tds[0].xpath(".//span[@class='medalcontainer']")
                img_url = None
                if medal_container:
                    img_url = medal_container[0].xpath(".//a[@class='medalimg']/@data-original")
                    if not img_url:
                        img_style = medal_container[0].xpath(".//img/@style")
                        if img_style:
                            style = img_style[0]
                            if "background-image: url('" in style:
                                img_url = [style.split("background-image: url('")[1].split("'")[0]]
                if img_url:
                    medal['imageSmall'] = img_url[0]
                else:
                    logger.warning("未找到勋章图片URL")
            except Exception as e:
                logger.error(f"处理勋章图片时发生错误: {str(e)}")
            
            # 2. 名称，b标签
            try:
                info_cell = tds[1]
                name = info_cell.xpath(".//b/text()")
                if name:
                    medal['name'] = name[0].strip()
                else:
                    logger.warning("未找到勋章名称")
            except Exception as e:
                logger.error(f"处理勋章名称时发生错误: {str(e)}")
            
            # 3. 描述，获取b标签后的第一个文本节点
            try:
                # 获取b标签后的第一个文本节点
                desc_text = info_cell.xpath("string(.//b/following-sibling::text()[1])").strip()
                
                # 如果文本包含购买时间，则只取前面的部分
                if "可购买时间" in desc_text:
                    desc_text = desc_text.split("可购买时间")[0].strip()
                
                # 如果文本为空、只包含括号或全是空白，则不写入description
                if desc_text and desc_text not in ['（', '(', '', None]:
                    medal['description'] = desc_text
            except Exception as e:
                logger.error(f"处理勋章描述时发生错误: {str(e)}")
            
            # 4. 可购买时间，正则提取
            try:
                desc_html = etree.tostring(info_cell, encoding='unicode', method='html')
                time_match = re.search(r'可购买时间:([^<\)]+)', desc_html)
                if time_match:
                    time_range = time_match.group(1).strip()
                    if '~' in time_range:
                        begin_time, end_time = time_range.split('~')
                        medal['saleBeginTime'] = begin_time.strip()
                        medal['saleEndTime'] = end_time.strip()
                else:
                    logger.warning("未找到购买时间范围")
            except Exception as e:
                logger.error(f"处理购买时间范围时发生错误: {str(e)}")
            
            # 5. 库存
            try:
                stock = tds[2].xpath('.//text()')
                if stock:
                    medal['stock'] = stock[0].strip()
                else:
                    logger.warning("未找到库存数量")
            except Exception as e:
                logger.error(f"处理库存数量时发生错误: {str(e)}")
            
            # 6. 单日限购
            try:
                daily_limit = tds[3].xpath('.//text()')
                if daily_limit:
                    medal['dailyLimit'] = daily_limit[0].strip()
                else:
                    logger.warning("未找到单日限购数量")
            except Exception as e:
                logger.error(f"处理单日限购数量时发生错误: {str(e)}")
            
            # 7. 价格
            try:
                price = tds[4].xpath('.//text()')
                if price:
                    price_text = price[0].strip().replace(',', '')
                    try:
                        medal['price'] = int(price_text)
                    except ValueError:
                        medal['price'] = 0
                        logger.warning(f"价格转换失败: {price_text}")
                else:
                    logger.warning("未找到价格")
            except Exception as e:
                logger.error(f"处理价格时发生错误: {str(e)}")
            
            # 9. 站点信息
            medal['site'] = site_name
            
            # 检查必要字段
            required_fields = ['name', 'price']
            missing_fields = [field for field in required_fields if field not in medal]
            if missing_fields:
                logger.error(f"勋章缺少必要字段: {missing_fields}")
                return None
                
            # 智能补全烧包乐园的purchase_status
            if site_name == '烧包乐园':
                stock = str(medal.get('stock', '')).strip()
                begin = medal.get('saleBeginTime', '').strip()
                end = medal.get('saleEndTime', '').strip()
                now = datetime.now()
                try:
                    if begin and end:
                        begin_dt = datetime.strptime(begin, "%Y-%m-%d %H:%M:%S")
                        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
                        if now > end_dt:
                            medal['purchase_status'] = '已过可购买时间'
                        elif stock == '0' and begin_dt <= now <= end_dt:
                            medal['purchase_status'] = '库存不足'
                        elif stock and stock != '0' and begin_dt <= now <= end_dt:
                            medal['purchase_status'] = '购买'
                except Exception as e:
                    pass  # 时间格式异常时忽略
                
            return self.format_medal_data(medal)
            
        except Exception as e:
            logger.error(f"处理勋章数据时发生未知错误: {str(e)}")
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
