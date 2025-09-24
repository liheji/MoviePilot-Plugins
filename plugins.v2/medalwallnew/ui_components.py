# -*- coding: utf-8 -*-
"""
勋章墙UI组件生成器
优化了原有的复杂字符串拼接逻辑，使用更清晰的组件化方式
"""
from typing import List, Dict
from app.log import logger


class MedalUIComponents:
    """勋章UI组件生成器"""
    
    @staticmethod
    def create_top_stats(site_count: int, medal_total: int, buy_count: int, 
                        owned_count: int, not_afford_count: int, not_buy_count: int, unknown_count: int) -> Dict:
        """创建顶部统计信息"""
        top_stats = [
            {'icon': 'mdi-office-building', 'color': '#16b1ff', 'value': site_count, 'label': '站点数量'},
            {'icon': 'mdi-medal', 'color': '#16b1ff', 'value': medal_total, 'label': '勋章总数'},
            {'icon': 'mdi-cart-check', 'color': '#a259e6', 'value': buy_count, 'label': '可购买'},
            {'icon': 'mdi-badge-account', 'color': '#ff357a', 'value': owned_count, 'label': '已拥有'},
            {'icon': 'mdi-money-off', 'color': '#ffb300', 'value': not_afford_count, 'label': '魔力值不足'},
            {'icon': 'mdi-cancel', 'color': '#ffb300', 'value': not_buy_count, 'label': '不可购买'},
            {'icon': 'mdi-help-circle-outline', 'color': '#ff5c5c', 'value': unknown_count, 'label': '未知状态'},
        ]
        
        return {
            'component': 'VCard',
            'props': {
                'variant': 'flat', 
                'color': 'surface', 
                'class': 'mb-4', 
                'style': 'border-radius: 14px; box-shadow: 0 1px 4px rgba(22,177,255,0.04); padding: 12px 12px 6px 12px;'
            },
            'content': [
                {
                    'component': 'VRow',
                    'props': {},
                    'content': [
                        {
                            'component': 'VCol',
                            'props': {'cols': 1.5, 'class': 'text-center px-1'},
                            'content': [
                                {'component': 'VIcon', 'props': {'size': '40', 'color': v['color'], 'class': 'mb-1'}, 'text': v['icon']},
                                {'component': 'div', 'props': {'class': 'font-weight-bold', 'style': 'font-size: 2rem; color: #222;'}, 'text': str(v['value'])},
                                {'component': 'div', 'props': {'class': 'text-body-2', 'style': 'color: #b0b0b0; font-size: 1rem; margin-top: 2px;'}, 'text': v['label']}
                            ]
                        } for v in top_stats
                    ]
                }
            ]
        }

    @staticmethod
    def create_site_section(site_id: str, site_name: str, medals: List[Dict]) -> Dict:
        """创建单个站点的UI部分"""
        total = len(medals)
        owned = sum(1 for m in medals if (m.get('purchase_status') or '').strip() in ['已经购买', '已拥有'])
        buy = sum(1 for m in medals if (m.get('purchase_status') or '').strip() in ['购买', '赠送'])
        not_afford = sum(1 for m in medals if (m.get('purchase_status') or '').strip() in ['魔力值不足'])
        not_buy = sum(1 for m in medals if (m.get('purchase_status') or '').strip() in ['已过可购买时间', '未到可购买时间', '需要更多工分', '需要更多魔力值', '需要更多蝌蚪', '库存不足', '仅授予'])

        # 站点行
        site_row = {
            'component': 'VRow',
            'props': {'class': 'align-center mb-1', 'style': 'background:#fafbfc; border-radius:10px; border-bottom:1px solid #ececec; padding:6px 14px 6px 14px;'},
            'content': [
                {'component': 'VCol', 'props': {'cols': 'auto', 'class': 'text-left d-flex align-center'}, 'content': [
                    {'component': 'VIcon', 'props': {'color': '#a259e6', 'size': '22', 'class': 'mr-2'}, 'text': 'mdi-crown'},
                    {'component': 'span', 'props': {'class': 'font-weight-bold', 'style': 'font-size:1.05rem; color:#222;'}, 'text': site_name}
                ]},
            ]
        }
        
        # 标签行
        chips_row = {
            'component': 'VRow',
            'props': {'class': 'justify-center mb-1'},
            'content': [
                {'component': 'VCol', 'props': {'cols': 'auto', 'class': 'd-flex justify-center align-center'}, 'content': [
                    {'component': 'VChip', 'props': {'color': '#e5e9fa', 'variant': 'flat', 'size': 'large', 'class': 'mr-14', 'style': 'font-size:0.92rem; font-weight:500; border-radius:18px; padding:6px 18px; min-height:36px;'}, 'content': [
                        {'component': 'VIcon', 'props': {'size': '20', 'color': '#a259e6', 'class': 'mr-1'}, 'text': 'mdi-medal'},
                        {'component': 'span', 'props': {}, 'text': f'勋章总数: {total}'}
                    ]},
                    {'component': 'VChip', 'props': {'color': '#e6f7ea', 'variant': 'flat', 'size': 'large', 'class': 'mr-14', 'style': 'font-size:0.92rem; font-weight:500; border-radius:18px; padding:6px 18px; min-height:36px;'}, 'content': [
                        {'component': 'VIcon', 'props': {'size': '20', 'color': '#43c04b', 'class': 'mr-1'}, 'text': 'mdi-badge-account'},
                        {'component': 'span', 'props': {}, 'text': f'已拥有: {owned}'}
                    ]},
                    {'component': 'VChip', 'props': {'color': '#e6f7ea', 'variant': 'flat', 'size': 'large', 'class': 'mr-14', 'style': 'font-size:0.92rem; font-weight:500; border-radius:18px; padding:6px 18px; min-height:36px;'}, 'content': [
                        {'component': 'VIcon', 'props': {'size': '20', 'color': '#43c04b', 'class': 'mr-1'}, 'text': 'mdi-cart-check'},
                        {'component': 'span', 'props': {}, 'text': f'可购买: {buy}'}
                    ]},
                    {'component': 'VChip', 'props': {'color': '#ffedc1', 'variant': 'flat', 'size': 'large', 'class': 'mr-14', 'style': 'font-size:0.92rem; font-weight:500; border-radius:18px; padding:6px 18px; min-height:36px;'}, 'content': [
                        {'component': 'VIcon', 'props': {'size': '20', 'color': '#ff5c5c', 'class': 'mr-1'}, 'text': 'mdi-money-off'},
                        {'component': 'span', 'props': {}, 'text': f'魔力值不足: {not_afford}'}
                    ]},
                    {'component': 'VChip', 'props': {'color': '#ffeaea', 'variant': 'flat', 'size': 'large', 'class': '', 'style': 'font-size:0.92rem; font-weight:500; border-radius:18px; padding:6px 18px; min-height:36px;'}, 'content': [
                        {'component': 'VIcon', 'props': {'size': '20', 'color': '#ff5c5c', 'class': 'mr-1'}, 'text': 'mdi-cancel'},
                        {'component': 'span', 'props': {}, 'text': f'不可购买: {not_buy}'}
                    ]}
                ]}
            ]
        }
        
        # 详情展开
        detail_content = MedalUIComponents._create_medal_details(medals)
        detail_row = {
            'component': 'VRow',
            'content': [
                {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                    {'component': 'VExpansionPanels', 'props': {'variant': 'accordion', 'class': 'elevation-0', 'style': 'background:transparent;'}, 'content': [
                        {
                            'component': 'VExpansionPanel',
                            'props': {'class': 'elevation-0', 'style': 'background:transparent;'},
                            'content': [
                                {'component': 'VExpansionPanelTitle', 'props': {'class': 'py-2', 'style': 'font-weight:500; font-size:1rem; color:#666;'}, 'content': [
                                    {'component': 'span', 'props': {'class': 'font-weight-bold'}, 'text': '勋章详情'}
                                ]},
                                {'component': 'VExpansionPanelText', 'props': {'class': 'py-2', 'style': 'background:#f7f8fa; border-radius:12px; padding:18px 12px 12px 12px;'}, 'content': detail_content}
                            ]
                        }
                    ]}
                ]}
            ]
        }
        
        # 用VCard包裹
        return {
            'component': 'VCard',
            'props': {'variant': 'flat', 'color': 'surface', 'class': 'mb-3', 'style': 'border-radius: 14px; box-shadow: 0 1px 4px rgba(22,177,255,0.04); padding: 12px 12px 6px 12px;'},
            'content': [site_row, chips_row, detail_row]
        }

    @staticmethod
    def _create_medal_details(medals: List[Dict]) -> List[Dict]:
        """创建勋章详情内容"""
        # 分类分组
        buyable_medals = [m for m in medals if (m.get('purchase_status') or '').strip() in ['购买', '赠送']]
        owned_medals = [m for m in medals if (m.get('purchase_status') or '').strip() in ['已经购买', '已拥有']]
        not_afford_medals = [m for m in medals if (m.get('purchase_status') or '').strip() in ['魔力值不足']]
        unavailable_medals = [m for m in medals if (m.get('purchase_status') or '').strip() in ['已过可购买时间', '未到可购买时间', '需要更多工分', '需要更多魔力值', '需要更多蝌蚪', '库存不足', '仅授予']]
        unknown_medals = [m for m in medals if not (m.get('purchase_status') or '').strip()]

        detail_content = []

        if buyable_medals:
            detail_content.append({'component': 'VCardTitle', 'props': {'class': 'mb-1', 'style': 'color:#43c04b; font-size:1rem; font-weight:600; text-align:left;'}, 'text': f'可购买（{len(buyable_medals)}）'})
            detail_content.append({'component': 'VRow', 'content': MedalUIComponents._get_medal_elements(buyable_medals)})

        if owned_medals:
            detail_content.append({'component': 'VCardTitle', 'props': {'class': 'mb-1', 'style': 'color:#43c04b; font-size:1rem; font-weight:600; text-align:left;'}, 'text': f'已拥有（{len(owned_medals)}）'})
            detail_content.append({'component': 'VRow', 'content': MedalUIComponents._get_medal_elements(owned_medals)})

        if not_afford_medals:
            detail_content.append({'component': 'VCardTitle', 'props': {'class': 'mb-1', 'style': 'color:#ffb300; font-size:1rem; font-weight:600; text-align:left;'}, 'text': f'魔力值不足（{len(not_afford_medals)}）'})
            detail_content.append({'component': 'VRow', 'content': MedalUIComponents._get_medal_elements(not_afford_medals)})

        if unavailable_medals:
            # 排序不可购买勋章
            def get_unavailable_priority(medal):
                status = (medal.get('purchase_status') or '').strip()
                if '已过可购买时间' in status:
                    return 1
                elif '未到可购买时间' in status:
                    return 2
                elif '需要更多' in status:
                    return 3
                elif '库存不足' in status:
                    return 4
                elif '仅授予' in status:
                    return 5
                else:
                    return 99
            unavailable_medals = sorted(unavailable_medals, key=get_unavailable_priority)
            detail_content.append({'component': 'VCardTitle', 'props': {'class': 'mb-1', 'style': 'color:#ff5c5c; font-size:1rem; font-weight:600; text-align:left;'}, 'text': f'不可购买（{len(unavailable_medals)}）'})
            detail_content.append({'component': 'VRow', 'content': MedalUIComponents._get_medal_elements(unavailable_medals)})
            
        if unknown_medals:
            detail_content.append({'component': 'VCardTitle', 'props': {'class': 'mb-1', 'style': 'color:#b0b0b0; font-size:1rem; font-weight:600; text-align:left;'}, 'text': f'未知状态（{len(unknown_medals)}）'})
            detail_content.append({'component': 'VRow', 'content': MedalUIComponents._get_medal_elements(unknown_medals)})
            
        return detail_content

    @staticmethod
    def _get_medal_elements(medals: List[Dict]) -> List[Dict]:
        """生成勋章卡片元素"""
        elements = []
        for medal in medals:
            status = (medal.get('purchase_status') or '').strip()
            chip_color = '#b0b0b0'  # 默认灰色
            chip_text = status or '未知'

            # 通用状态判断逻辑

            # 其余原有逻辑...
            if chip_text in ['购买', '赠送']:
                chip_color = '#43c04b'
            elif chip_text in ['已经购买', '已拥有']:
                chip_color = '#43c04b'
                chip_text = '已拥有'
            elif chip_text in ['魔力值不足']:
                chip_color = '#ffb300'
            elif chip_text in ['已过可购买时间', '未到可购买时间', '需要更多工分', '需要更多魔力值', '需要更多蝌蚪', '仅授予', '库存不足']:
                chip_color = '#ff5c5c'
            else:
                chip_color = '#b0b0b0'
                chip_text = chip_text or '未知'

            price = medal.get('price', 0)
            price_str = f"价格：{price:,}" if price else ""

            # 属性区
            attrs = MedalUIComponents._create_medal_attributes(medal)
            
            # 主标题栏
            title_content = [
                {
                    'component': 'div',
                    'props': {
                        'style': 'max-width:240px; box-sizing:border-box; overflow:hidden; text-overflow:ellipsis; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; word-break:break-all; overflow-wrap:break-word; white-space:normal; font-size:1.1rem; text-align:center; height:2.2em; line-height:1.1em; position:relative; margin:auto;'
                    },
                    'text': f"《{medal.get('name','')}》"
                }
            ]
            
            card = {
                'component': 'VCol',
                'props': {'cols': 12, 'sm': 6, 'md': 4, 'lg': 3, 'class': 'mb-3 d-flex justify-center'},
                'content': [
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'flat',
                            'class': 'pa-4 d-flex flex-column align-center',
                            'style': 'border-radius: 16px; box-shadow: 0 2px 8px rgba(22,177,255,0.08); min-width:220px; max-width:270px; min-height:340px; display:flex; flex-direction:column; justify-content:center; align-items:center;'
                        },
                        'content': [
                            # 顶部名称
                            {
                                'component': 'VCardTitle',
                                'props': {'class': 'text-center font-weight-bold', 'style': 'margin-top:0; padding-top:0px; margin-bottom:2px;'},
                                'content': title_content
                            },
                            # 描述（多行省略）
                            {
                                'component': 'div',
                                'props': {
                                    'style': 'color:#888; margin:0 0 4px 0; padding:0; width:100%; max-width:100%; box-sizing:border-box; overflow:hidden; text-overflow:ellipsis; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; word-break:break-all; font-size:0.7rem; text-align:center;'
                                },
                                'text': medal.get('description','')
                            },
                            # 图片
                            {
                                'component': 'VImg',
                                'props': {
                                    'src': medal.get('imageSmall',''),
                                    'alt': medal.get('name',''),
                                    'width': '90',
                                    'height': '90',
                                    'class': 'my-2 mx-auto',
                                    'style': 'border-radius:50%; background:#f7f8fa; box-shadow:0 1px 4px rgba(22,177,255,0.04);'
                                }
                            },
                            # 属性区
                            {
                                'component': 'VRow',
                                'props': {'class': 'mt-2 mb-1', 'style': 'width:100%'},
                                'content': attrs
                            },
                            # 底部价格+状态
                            {
                                'component': 'VRow',
                                'props': {'class': 'mt-0 align-center', 'style': 'width:100%'},
                                'content': [
                                    {
                                        'component': 'VCol',
                                        'props': {'cols': 12, 'class': 'py-0', 'style': 'display:flex; align-items:center;'},
                                        'content': [
                                            {'component': 'div', 'props': {'class': 'text-body-2 font-weight-bold', 'style': 'color:#43c04b; font-size:0.9rem;'}, 'text': price_str},
                                            {'component': 'div', 'props': {'style': 'margin-left:auto;'}, 'content': [
                                                {
                                                    'component': 'VChip',
                                                    'props': {
                                                        'color': chip_color,
                                                        'variant': 'flat',
                                                        'size': 'small',
                                                        'class': 'font-weight-bold',
                                                        'style': 'color:#fff; border-radius:12px; padding:2px 10px; white-space:nowrap; font-size:0.75rem; display:inline-block; line-height:1.9; min-width:unset; max-width:unset; width:auto;'
                                                    },
                                                    'text': chip_text
                                                }
                                            ]}
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
            elements.append(card)
        return elements


    @staticmethod
    def _create_medal_attributes(medal: Dict) -> List[Dict]:
        """创建勋章属性区域（通用模板）"""
        attrs = [
            {
                'component': 'VCol',
                'props': {'cols': 12, 'class': 'py-0'},
                'content': [
                    {'component': 'div', 'props': {'class': 'text-caption', 'style': 'color:#666;'}, 'text': f"站点：{medal.get('site','')}"}
                ]
            }
        ]
        
        # 通用属性显示
        if medal.get('saleBeginTime') or medal.get('saleEndTime'):
            attrs.append({
                'component': 'VCol',
                'props': {'cols': 12, 'class': 'py-0'},
                'content': [
                    {'component': 'div', 'props': {'class': 'text-caption', 'style': 'color:#666;'}, 'text': f"销售时间：{medal.get('saleBeginTime','')} ~ {medal.get('saleEndTime','')}"}
                ]
            })
        
        if medal.get('validity'):
            attrs.append({
                'component': 'VCol',
                'props': {'cols': 12, 'class': 'py-0'},
                'content': [
                    {'component': 'div', 'props': {'class': 'text-caption', 'style': 'color:#666;'}, 'text': f"有效期：{medal.get('validity','')}"}
                ]
            })
        
        if medal.get('stock'):
            attrs.append({
                'component': 'VCol',
                'props': {'cols': 12, 'class': 'py-0'},
                'content': [
                    {'component': 'div', 'props': {'class': 'text-caption', 'style': 'color:#666;'}, 'text': f"库存：{medal.get('stock','')}"}
                ]
            })
        
        if medal.get('bonus_rate'):
            bonus_style = 'color:#666;'
            try:
                if float(medal.get('bonus_rate', '').replace('%', '')) < 0:
                    bonus_style = 'font-weight:bold;color:rgb(255, 92, 92);'
            except Exception:
                pass
            attrs.append({
                'component': 'VCol',
                'props': {'cols': 12, 'class': 'py-0'},
                'content': [
                    {'component': 'div', 'props': {'class': 'text-caption', 'style': f'{bonus_style}'}, 'text': f"魔力加成：{medal.get('bonus_rate','')}"}
                ]
            })
        
        return attrs
