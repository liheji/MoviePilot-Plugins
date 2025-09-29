# -*- coding: utf-8 -*-
"""
站点开注检查UI组件生成器
"""
from typing import List, Dict
from app.log import logger


class SiteCheckUIComponents:
    """站点开注检查UI组件生成器"""
    
    @staticmethod
    def create_top_stats(total_sites: int, open_count: int, closed_count: int, error_count: int) -> Dict:
        """创建顶部统计信息"""
        top_stats = [
            {'icon': 'mdi-web', 'color': '#16b1ff', 'value': total_sites, 'label': '站点总数'},
            {'icon': 'mdi-check-circle', 'color': '#4caf50', 'value': open_count, 'label': '开注站点'},
            {'icon': 'mdi-close-circle', 'color': '#f44336', 'value': closed_count, 'label': '关闭注册'},
            {'icon': 'mdi-alert-circle', 'color': '#ff9800', 'value': error_count, 'label': '异常站点'},
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
                            'props': {'cols': 3, 'class': 'text-center px-1'},
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

        # 开注站点
        if open_sites:
            site_rows.append(SiteCheckUIComponents._create_site_group(
                "开注站点", open_sites, "success", "mdi-check-circle"
            ))

        # 关闭注册站点
        if closed_sites:
            site_rows.append(SiteCheckUIComponents._create_site_group(
                "关闭注册", closed_sites, "error", "mdi-close-circle"
            ))

        # 异常站点
        if error_sites:
            site_rows.append(SiteCheckUIComponents._create_site_group(
                "异常站点", error_sites, "warning", "mdi-alert-circle"
            ))

        # 未知状态站点
        if unknown_sites:
            site_rows.append(SiteCheckUIComponents._create_site_group(
                "未知状态", unknown_sites, "info", "mdi-help-circle"
            ))

        return site_rows

    @staticmethod
    def _create_site_group(title: str, sites: List[Dict], color: str, icon: str) -> Dict:
        """创建站点分组"""
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
                    'props': {
                        'class': 'd-flex align-center pa-4'
                    },
                    'content': [
                        {
                            'component': 'VIcon',
                            'props': {
                                'color': color,
                                'class': 'mr-2',
                                'size': 'small'
                            },
                            'text': icon
                        },
                        {
                            'component': 'span',
                            'props': {
                                'class': 'font-weight-medium'
                            },
                            'text': f'{title} ({len(sites)})'
                        }
                    ]
                },
                {
                    'component': 'VCardText',
                    'props': {
                        'class': 'pa-3'
                    },
                    'content': [
                        {
                            'component': 'VExpansionPanels',
                            'props': {
                                'variant': 'accordion',
                                'class': 'mt-2'
                            },
                            'content': [
                                {
                                    'component': 'VExpansionPanel',
                                    'props': {
                                        'class': 'elevation-0',
                                        'style': 'background:transparent;'
                                    },
                                    'content': [
                                        {
                                            'component': 'VExpansionPanelTitle',
                                            'props': {
                                                'class': 'py-2',
                                                'style': 'font-weight:500; font-size:1rem; color:#666;'
                                            },
                                            'content': [
                                                {
                                                    'component': 'span',
                                                    'props': {
                                                        'class': 'font-weight-bold'
                                                    },
                                                    'text': f'查看 {len(sites)} 个站点详情'
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'VExpansionPanelText',
                                            'props': {
                                                'class': 'py-2',
                                                'style': 'background:#f7f8fa; border-radius:12px; padding:18px 12px 12px 12px;'
                                            },
                                            'content': SiteCheckUIComponents._create_site_items(sites)
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
    def _create_site_items(sites: List[Dict]) -> List[Dict]:
        """创建站点项目列表"""
        items = []
        for site in sites:
            status = site.get('status', 'unknown')
            status_color = SiteCheckUIComponents._get_status_color(status)
            status_icon = SiteCheckUIComponents._get_status_icon(status)
            
            items.append({
                'component': 'VCard',
                'props': {
                    'variant': 'flat',
                    'class': 'mb-2',
                    'style': 'border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);'
                },
                'content': [
                    {
                        'component': 'VCardText',
                        'props': {
                            'class': 'pa-3'
                        },
                        'content': [
                            {
                                'component': 'VRow',
                                'props': {
                                    'class': 'align-center'
                                },
                                'content': [
                                    {
                                        'component': 'VCol',
                                        'props': {
                                            'cols': 'auto',
                                            'class': 'd-flex align-center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VIcon',
                                                'props': {
                                                    'color': status_color,
                                                    'size': '24',
                                                    'class': 'mr-2'
                                                },
                                                'text': status_icon
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VCol',
                                        'props': {
                                            'class': 'flex-grow-1'
                                        },
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'font-weight-bold text-h6',
                                                    'style': 'color: #222;'
                                                },
                                                'text': site.get('name', site.get('domain', '未知站点'))
                                            },
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-caption',
                                                    'style': 'color: #666; margin-top: 2px;'
                                                },
                                                'text': site.get('message', '无详细信息')
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VCol',
                                        'props': {
                                            'cols': 'auto'
                                        },
                                        'content': [
                                            {
                                                'component': 'VBtn',
                                                'props': {
                                                    'color': 'primary',
                                                    'variant': 'outlined',
                                                    'size': 'small',
                                                    'href': site.get('url', '#'),
                                                    'target': '_blank',
                                                    'prepend-icon': 'mdi-open-in-new'
                                                },
                                                'text': '访问站点'
                                            }
                                        ]
                                    }
                                ]
                            },
                            {
                                'component': 'VRow',
                                'props': {
                                    'class': 'mt-2'
                                },
                                'content': [
                                    {
                                        'component': 'VCol',
                                        'props': {
                                            'cols': 12
                                        },
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-caption',
                                                    'style': 'color: #999;'
                                                },
                                                'text': f"检查时间: {site.get('check_time', '未知')}"
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            })
        
        return items

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
