# 标准库
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# 第三方库
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# 应用程序
from app.core.config import settings
from app.core.event import eventmanager
from app.helper.sites import SitesHelper
from app.helper.browser import PlaywrightHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.types import EventType
from app.utils.http import RequestUtils


class SiteOpenCheck(_PluginBase):
    # 插件名称
    plugin_name = "站点开注检查"
    # 插件描述
    plugin_desc = "检查各个站点是否开放注册，通过访问 /signup.php 页面判断开注状态。\n支持 requests 和 PlaywrightHelper 两种方式获取页面内容。"
    # 插件图标
    plugin_icon = "signin.png"
    # 插件版本
    plugin_version = "0.7"
    # 插件作者
    plugin_author = "liheji"
    # 作者主页
    author_url = "https://github.com/liheji"
    # 插件配置项ID前缀
    plugin_config_prefix = "sitechecknew_"
    # 加载顺序
    plugin_order = 10
    # 可使用的用户级别
    auth_level = 2

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None
    # 站点助手实例
    sites: SitesHelper = None

    # 配置属性
    _enabled: bool = False
    _cron: str = ""
    _onlyonce: bool = False
    _notify: bool = False
    _use_playwright: bool = False
    _timeout: int = 15
    _retry_times: int = 3
    _retry_interval: int = 5

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        # 停止现有任务
        self.stop_service()

        # 初始化助手
        self.sites = SitesHelper()

        # 配置
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce")
            self._notify = config.get("notify")
            self._use_playwright = config.get("use_playwright", False)
            self._timeout = config.get("timeout", 15)
            self._retry_times = config.get("retry_times", 3)
            self._retry_interval = config.get("retry_interval", 5)

            # 保存配置
            self.__update_config()

        # 立即运行一次
        if self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            logger.info("站点开注检查服务启动，立即运行一次")
            self._scheduler.add_job(func=self.__check_all_sites, trigger='date',
                                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                name="站点开注检查")

            # 关闭一次性开关
            self._onlyonce = False
            # 保存配置
            self.__update_config()

            # 启动任务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

    def __update_config(self):
        """保存配置"""
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "notify": self._notify,
            "use_playwright": self._use_playwright,
            "cron": self._cron,
            "retry_times": self._retry_times,
            "retry_interval": self._retry_interval,
            "timeout": self._timeout
        })

    def get_service(self) -> List[Dict[str, Any]]:
        """注册插件公共服务"""
        if self._enabled and self._cron:
            return [{
                "id": "SiteCheckNew",
                "name": "站点开注检查 - 定时任务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__check_all_sites,
                "kwargs": {}
            }]
        return []

    def __check_all_sites(self):
        """检查所有站点的开注状态"""
        logger.info("开始检查所有站点的开注状态")
        try:
            # 获取所有站点
            all_sites = self.sites.get_indexers()
            if not all_sites:
                logger.error("未获取到站点信息")
                return

            # 存储检查结果
            check_results = []
            open_sites = []
            closed_sites = []
            error_sites = []

            # 遍历所有站点
            for site_info in all_sites:
                try:
                    domain = site_info.get("id", "")
                    site_name = site_info.get("name", domain)
                    site_url = site_info.get("url", f"https://{domain}")
                    
                    # 检查站点开注状态
                    status, message = self.__check_site_registration(site_url, site_name)
                    
                    result = {
                        "domain": domain,
                        "name": site_name,
                        "url": site_url,
                        "status": status,
                        "message": message,
                        "check_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    check_results.append(result)
                    
                    if status == "open":
                        open_sites.append(result)
                    elif status == "closed":
                        closed_sites.append(result)
                    else:
                        error_sites.append(result)
                        
                except Exception as e:
                    logger.error(f"检查站点 {site_info.get('id', 'unknown')} 时发生错误: {str(e)}")
                    error_sites.append({
                        "domain": site_info.get("id", ""),
                        "name": site_info.get("name", site_info.get("id", "unknown")),
                        "url": site_info.get("url", f"https://{site_info.get('id', 'unknown')}"),
                        "status": "error",
                        "message": f"检查失败: {str(e)}",
                        "check_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })

            # 保存检查结果
            self.save_data('check_results', check_results, 'sitecheck')
            self.save_data('open_sites', open_sites, 'sitecheck')
            self.save_data('closed_sites', closed_sites, 'sitecheck')
            self.save_data('error_sites', error_sites, 'sitecheck')

            # 发送通知
            if self._notify:
                self.__send_notification(len(check_results), len(open_sites), len(closed_sites), len(error_sites))

            logger.info(f"站点开注检查完成，共检查 {len(check_results)} 个站点，开注 {len(open_sites)} 个，关闭 {len(closed_sites)} 个，异常 {len(error_sites)} 个")

        except Exception as e:
            logger.error(f"检查所有站点时发生错误: {str(e)}")

    def __check_site_registration(self, site_url: str, site_name: str) -> Tuple[str, str]:
        """检查单个站点的注册状态"""
        try:
            # 构建注册页面URL
            signup_url = f"{site_url.rstrip('/')}/signup.php"
            
            # 获取页面内容
            if self._use_playwright:
                page_source = PlaywrightHelper().get_page_source(
                    url=signup_url,
                    cookies=None,  # 不携带cookie
                    ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    proxies=None,  # 不使用代理
                    timeout=self._timeout
                )
            else:
                res = RequestUtils(
                    ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    proxies=None,  # 不使用代理
                    timeout=self._timeout
                ).get_res(url=signup_url)
                
                if not res or res.status_code != 200:
                    return "error", f"无法访问注册页面，状态码: {res.status_code if res else 'None'}"
                
                page_source = res.text

            if not page_source:
                return "error", "无法获取页面内容"

            # 检查关闭注册的关键词
            closed_keywords = [
                "自由注册当前关闭",
                "对不起",
                "抱歉",
                "注册已关闭",
                "暂不开放注册",
                "注册功能暂时关闭"
            ]
            
            for keyword in closed_keywords:
                if keyword in page_source:
                    return "closed", f"检测到关闭注册关键词: {keyword}"

            # 检查开放注册的关键词
            open_keywords = [
                'type="submit"',
                'button',
                '注册'
            ]
            
            # 检查是否有提交按钮或包含注册的按钮
            if 'type="submit"' in page_source:
                return "open", "检测到提交按钮，可能开放注册"
            
            # 检查包含"注册"的按钮
            if re.search(r'<button[^>]*>.*注册.*</button>', page_source, re.IGNORECASE):
                return "open", "检测到注册按钮，可能开放注册"
            
            # 检查包含"注册"的输入框
            if re.search(r'<input[^>]*>.*注册.*</input>', page_source, re.IGNORECASE):
                return "open", "检测到注册输入框，可能开放注册"

            # 如果没有明确的开放或关闭标识，返回未知
            return "unknown", "无法确定注册状态"

        except Exception as e:
            logger.error(f"检查站点 {site_name} 注册状态时发生错误: {str(e)}")
            return "error", f"检查失败: {str(e)}"

    def __send_notification(self, total: int, open_count: int, closed_count: int, error_count: int):
        """发送通知消息"""
        text_message = f"站点开注检查完成！\n\n"
        text_message += f"总站点数: {total}\n"
        text_message += f"开注站点: {open_count}\n"
        text_message += f"关闭注册: {closed_count}\n"
        text_message += f"异常站点: {error_count}\n\n"
        text_message += f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        self.post_message(
            mtype=NotificationType.SiteMessage,
            title="【站点开注检查】检查完成",
            text=text_message
        )

    def stop_service(self) -> None:
        """停止服务"""
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        """
        return [{
            "path": "/check_sites",
            "endpoint": self.__check_all_sites,
            "methods": ["GET"],
            "summary": "手动检查所有站点开注状态",
            "description": "立即执行一次站点开注检查",
        }]

    def get_page(self) -> list:
        """获取页面数据"""
        try:
            from .ui_components import SiteCheckUIComponents

            # 获取检查结果
            check_results = self.get_data('check_results', 'sitecheck') or []
            open_sites = self.get_data('open_sites', 'sitecheck') or []
            closed_sites = self.get_data('closed_sites', 'sitecheck') or []
            error_sites = self.get_data('error_sites', 'sitecheck') or []

            # 统计信息
            total_sites = len(check_results)
            open_count = len(open_sites)
            closed_count = len(closed_sites)
            error_count = len(error_sites)

            # 创建顶部统计信息
            top_row = SiteCheckUIComponents.create_top_stats(
                total_sites, open_count, closed_count, error_count
            )

            # 创建站点列表
            site_rows = []
            if check_results:
                site_rows = SiteCheckUIComponents.create_site_list(check_results)

            # 页面结构
            return [top_row] + site_rows

        except Exception as e:
            logger.error(f"生成站点检查页面时发生错误: {str(e)}")
            return [{
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {'cols': 12, 'md': 12},
                        'content': [
                            {'component': 'VAlert', 'props': {'type': 'error', 'variant': 'tonal', 'text': f'生成页面时发生错误: {str(e)}'}}
                        ]
                    }
                ]
            }]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """拼装插件配置页面"""
        # 动态判断MoviePilot版本，决定定时任务输入框组件类型
        version = getattr(settings, "VERSION_FLAG", "v1")
        cron_field_component = "VCronField" if version == "v2" else "VTextField"

        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'flat',
                            'class': 'mb-6',
                            'color': 'surface'
                        },
                        'content': [
                            {
                                'component': 'VCardItem',
                                'props': {
                                    'class': 'pa-6'
                                },
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {
                                            'class': 'd-flex align-center text-h6'
                                        },
                                        'content': [
                                            {
                                                'component': 'VIcon',
                                                'props': {
                                                    'style': 'color: #16b1ff',
                                                    'class': 'mr-3',
                                                    'size': 'default'
                                                },
                                                'text': 'mdi-cog'
                                            },
                                            {
                                                'component': 'span',
                                                'text': '基本设置'
                                            }
                                        ]
                                    }
                                ]
                            },
                            {
                                'component': 'VCardText',
                                'props': {
                                    'class': 'px-6 pb-6'
                                },
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'sm': 3
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'enabled',
                                                            'label': '启用插件',
                                                            'color': 'primary',
                                                            'hide-details': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'sm': 3
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'notify',
                                                            'label': '开启通知',
                                                            'color': 'primary',
                                                            'hide-details': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'sm': 3
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'use_playwright',
                                                            'label': '使用浏览器仿真',
                                                            'color': 'primary',
                                                            'hide-details': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'sm': 3
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'onlyonce',
                                                            'label': '立即运行一次',
                                                            'color': 'primary',
                                                            'hide-details': True
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'flat',
                            'class': 'mb-6',
                            'color': 'surface'
                        },
                        'content': [
                            {
                                'component': 'VCardItem',
                                'props': {
                                    'class': 'pa-6'
                                },
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {
                                            'class': 'd-flex align-center text-h6'
                                        },
                                        'content': [
                                            {
                                                'component': 'VIcon',
                                                'props': {
                                                    'style': 'color: #16b1ff',
                                                    'class': 'mr-3',
                                                    'size': 'default'
                                                },
                                                'text': 'mdi-timer'
                                            },
                                            {
                                                'component': 'span',
                                                'text': '定时设置'
                                            }
                                        ]
                                    }
                                ]
                            },
                            {
                                'component': 'VCardText',
                                'props': {
                                    'class': 'px-6 pb-6'
                                },
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'sm': 4
                                                },
                                                'content': [
                                                    {
                                                        'component': cron_field_component,
                                                        'props': {
                                                            'model': 'cron',
                                                            'label': '执行周期(Cron)',
                                                            'placeholder': '5位cron表达式，默认每天9点执行',
                                                            'variant': 'outlined',
                                                            'color': 'primary',
                                                            'hide-details': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'sm': 4
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSelect',
                                                        'props': {
                                                            'model': 'retry_times',
                                                            'label': '重试次数',
                                                            'items': [
                                                                {'title': '1次', 'value': 1},
                                                                {'title': '2次', 'value': 2},
                                                                {'title': '3次', 'value': 3}
                                                            ],
                                                            'variant': 'outlined',
                                                            'color': 'primary',
                                                            'hide-details': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'sm': 4
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSelect',
                                                        'props': {
                                                            'model': 'timeout',
                                                            'label': '超时时间(秒)',
                                                            'items': [
                                                                {'title': '10秒', 'value': 10},
                                                                {'title': '15秒', 'value': 15},
                                                                {'title': '30秒', 'value': 30}
                                                            ],
                                                            'variant': 'outlined',
                                                            'color': 'primary',
                                                            'hide-details': True
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "notify": False,
            "use_playwright": False,
            "cron": "0 9 * * *",
            "retry_times": 3,
            "retry_interval": 5,
            "timeout": 15
        }
