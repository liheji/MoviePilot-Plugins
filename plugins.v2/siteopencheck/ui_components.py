# -*- coding: utf-8 -*-
"""
站点开注检查UI组件生成器 (修复版)
1. 修复点击分页无数据问题：确保 VRow 被包裹在 List 中。
2. 分页逻辑内嵌。
3. 网格布局：一行4个。
"""
from typing import List, Dict


class SiteOpenCheckUIComponents:
    """站点开注检查UI组件生成器"""

    # 每页显示数量，建议设为4的倍数
    PAGE_SIZE = 4

    @staticmethod
    def create_top_stats(sites: List[Dict]) -> Dict:
        """创建顶部统计信息"""
        total, open, closed, error, unknown = len(sites), 0, 0, 0, 0

        for s in sites:
            if s.get('status') == 'open':
                open += 1
            elif s.get('status') == 'closed':
                closed += 1
            elif s.get('status') == 'error':
                error += 1
            else:
                unknown += 1

        top_stats = [
            {'icon': 'mdi-web', 'color': '#16b1ff', 'value': total, 'label': '站点总数'},
            {'icon': 'mdi-check-circle', 'color': '#4caf50', 'value': open, 'label': '开注站点'},
            {'icon': 'mdi-close-circle', 'color': '#f44336', 'value': closed, 'label': '关闭注册'},
            {'icon': 'mdi-alert-circle', 'color': '#ff9800', 'value': error, 'label': '异常站点'},
            {'icon': 'mdi-help-circle', 'color': '#16B1FF', 'value': unknown, 'label': '未知状态'},
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
                            'props': {'cols': 2.4, 'class': 'text-center px-1'},
                            'content': [
                                {'component': 'VIcon', 'props': {'size': '40', 'color': v['color'], 'class': 'mb-1'},
                                 'text': v['icon']},
                                {'component': 'div',
                                 'props': {'class': 'font-weight-bold', 'style': 'font-size: 2rem; color: #222;'},
                                 'text': str(v['value'])},
                                {'component': 'div', 'props': {'class': 'text-body-2',
                                                               'style': 'color: #b0b0b0; font-size: 1rem; margin-top: 2px;'},
                                 'text': v['label']}
                            ]
                        } for v in top_stats
                    ]
                }
            ]
        }

    @staticmethod
    def create_site_list(sites: List[Dict]) -> List[Dict]:
        """创建站点列表"""
        if not sites:
            return [{
                'component': 'VAlert',
                'props': {
                    'type': 'info',
                    'text': '暂无站点数据',
                    'variant': 'tonal',
                    'class': 'mt-4',
                    'prepend-icon': 'mdi-information'
                }
            }]

        # 按状态分组
        open_sites = [s for s in sites if s.get('status') == 'open']
        closed_sites = [s for s in sites if s.get('status') == 'closed']
        error_sites = [s for s in sites if s.get('status') == 'error']
        unknown_sites = [s for s in sites if s.get('status') == 'unknown']

        site_rows = []

        if open_sites:
            site_rows.append(
                SiteOpenCheckUIComponents._create_site_group("开注站点", open_sites, "success", "mdi-check-circle"))
        if closed_sites:
            site_rows.append(
                SiteOpenCheckUIComponents._create_site_group("关闭注册", closed_sites, "error", "mdi-close-circle"))
        if error_sites:
            site_rows.append(
                SiteOpenCheckUIComponents._create_site_group("异常站点", error_sites, "warning", "mdi-alert-circle"))
        if unknown_sites:
            site_rows.append(
                SiteOpenCheckUIComponents._create_site_group("未知状态", unknown_sites, "info", "mdi-help-circle"))

        return site_rows

    @staticmethod
    def _create_site_group(title: str, sites: List[Dict], color: str, icon: str) -> Dict:
        """创建站点分组（外层：只包含一个总览面板）"""
        return {
            'component': 'VCard',
            'props': {
                'variant': 'flat',
                'color': 'surface',
                'class': 'mb-3',
                'style': 'border-radius: 14px; box-shadow: 0 1px 4px rgba(22,177,255,0.04); padding: 12px 12px 6px 12px;'
            },
            'content': [
                {
                    'component': 'VCardTitle',
                    'props': {'class': 'd-flex align-center pa-4'},
                    'content': [
                        {'component': 'VIcon', 'props': {'color': color, 'class': 'mr-2', 'size': 'small'},
                         'text': icon},
                        {'component': 'span', 'props': {'class': 'font-weight-medium'},
                         'text': f'{title} ({len(sites)})'}
                    ]
                },
                {
                    'component': 'VCardText',
                    'props': {'class': 'pa-3'},
                    'content': [
                        {
                            'component': 'VExpansionPanels',
                            'props': {'variant': 'accordion', 'class': 'mt-2'},
                            'content': [
                                {
                                    'component': 'VExpansionPanel',
                                    'props': {'class': 'elevation-0', 'style': 'background:transparent;'},
                                    'content': [
                                        {
                                            'component': 'VExpansionPanelTitle',
                                            'props': {'class': 'py-2',
                                                      'style': 'font-weight:500; font-size:1rem; color:#666;'},
                                            'content': [
                                                {'component': 'span', 'props': {'class': 'font-weight-bold'},
                                                 'text': f'查看 {len(sites)} 个站点详情'}
                                            ]
                                        },
                                        {
                                            'component': 'VExpansionPanelText',
                                            'props': {'class': 'py-2',
                                                      'style': 'background:#f7f8fa; border-radius:12px; padding:12px;'},
                                            'content': SiteOpenCheckUIComponents._create_inner_content(sites)
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }

    @staticmethod
    def _create_inner_content(sites: List[Dict]) -> List[Dict]:
        """
        生成内部内容（处理分页逻辑）
        如果站点数 > PAGE_SIZE，则嵌套一层 VExpansionPanels 用于分页
        否则直接显示网格
        """
        if len(sites) > SiteOpenCheckUIComponents.PAGE_SIZE:
            # 需要分页
            chunks = [sites[i:i + SiteOpenCheckUIComponents.PAGE_SIZE] for i in
                      range(0, len(sites), SiteOpenCheckUIComponents.PAGE_SIZE)]

            panels = []
            for idx, chunk in enumerate(chunks):
                start_num = idx * SiteOpenCheckUIComponents.PAGE_SIZE + 1
                end_num = start_num + len(chunk) - 1

                panels.append({
                    'component': 'VExpansionPanel',
                    'props': {'class': 'mb-2', 'style': 'background: #ffffff; border: 1px solid #eee;'},
                    'content': [
                        {
                            'component': 'VExpansionPanelTitle',
                            'props': {'class': 'py-1 px-2', 'style': 'min-height: 36px; font-size: 0.9rem;'},
                            'content': [
                                {'component': 'span', 'props': {'style': 'color: #555;'},
                                 'text': f'第 {idx + 1} 页 ({start_num}-{end_num})'}
                            ]
                        },
                        {
                            'component': 'VExpansionPanelText',
                            'props': {'class': 'py-2', 'eager': True},  # eager 确保内容加载
                            # _render_site_grid 返回一个列表，这里直接赋值给 content
                            'content': SiteOpenCheckUIComponents._render_site_grid(chunk)
                        }
                    ]
                })

            return [{
                'component': 'VExpansionPanels',
                'props': {'variant': 'accordion', 'class': 'elevation-0'},
                'content': panels
            }]
        else:
            # 不需要分页，直接渲染网格
            return SiteOpenCheckUIComponents._render_site_grid(sites)

    @staticmethod
    def _render_site_grid(sites: List[Dict]) -> List[Dict]:
        """
        渲染站点网格布局 (一行4个)
        【关键修复】返回值必须是 List，不能是单个 Dict
        """
        cards = [SiteOpenCheckUIComponents._create_site_card(site) for site in sites]

        # 将 VRow 包裹在列表中返回，VExpansionPanelText 的 content 才能正确渲染
        return [
            {
                'component': 'VRow',
                'content': cards
            }
        ]

    @staticmethod
    def _create_site_card(site: Dict) -> Dict:
        """创建单个站点的竖向卡片"""
        status = site.get('status', 'unknown')
        status_color = SiteOpenCheckUIComponents._get_status_color(status)
        status_icon = SiteOpenCheckUIComponents._get_status_icon(status)

        return {
            'component': 'VCol',
            # 响应式网格：手机1列，平板2列，大屏4列
            'props': {'cols': 12, 'sm': 6, 'md': 4, 'lg': 3},
            'content': [
                {
                    'component': 'VCard',
                    'props': {
                        'variant': 'flat',
                        'class': 'h-100 d-flex flex-column',
                        'style': 'border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border: 1px solid #eee;'
                    },
                    'content': [
                        {
                            'component': 'div',
                            'props': {'class': 'd-flex align-center pa-3 pb-1'},
                            'content': [
                                {'component': 'VIcon', 'props': {'color': status_color, 'size': '20', 'class': 'mr-2'},
                                 'text': status_icon},
                                {
                                    'component': 'div',
                                    'props': {'class': 'font-weight-bold text-truncate',
                                              'style': 'color: #222; font-size: 0.95rem;'},
                                    'text': site.get('name', site.get('domain', '未知站点'))
                                }
                            ]
                        },
                        {
                            'component': 'div',
                            'props': {'class': 'px-3 pb-2 flex-grow-1 text-caption',
                                      'style': 'color: #666; font-size: 0.8rem;'},
                            'text': site.get('message', '无详细信息')
                        },
                        {
                            'component': 'div',
                            'props': {'class': 'pa-2 pt-0 mt-auto d-flex align-center'},
                            'content': [
                                {
                                    'component': 'VBtn',
                                    'props': {
                                        'color': 'primary',
                                        'variant': 'text',
                                        'size': 'small',
                                        'class': 'px-1',
                                        'href': site.get('signup_url', site.get('url', '#')),
                                        'target': '_blank'
                                    },
                                    'text': '访问'
                                },
                                {
                                    'component': 'VSpacer'
                                },
                                {
                                    'component': 'span',
                                    'props': {'class': 'text-caption', 'style': 'color: #aaa; font-size: 0.7rem;'},
                                    'text': site.get('check_time', '未知')
                                }
                            ]
                        }
                    ]
                }
            ]
        }

    @staticmethod
    def _get_status_color(status: str) -> str:
        """获取状态颜色"""
        color_map = {
            'open': '#4caf50',
            'closed': '#f44336',
            'error': '#ff9800',
            'unknown': '#9e9e9e'
        }
        return color_map.get(status, '#9e9e9e')

    @staticmethod
    def _get_status_icon(status: str) -> str:
        """获取状态图标"""
        icon_map = {
            'open': 'mdi-check-circle',
            'closed': 'mdi-close-circle',
            'error': 'mdi-alert-circle',
            'unknown': 'mdi-help-circle'
        }
        return icon_map.get(status, 'mdi-help-circle')
