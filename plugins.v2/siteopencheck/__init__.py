# 标准库
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
from app.helper.module import ModuleHelper
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
    plugin_version = "2.3"
    # 插件作者
    plugin_author = "liheji"
    # 作者主页
    author_url = "https://github.com/liheji"
    # 插件配置项ID前缀
    plugin_config_prefix = "siteopencheck_"
    # 加载顺序
    plugin_order = 10
    # 可使用的用户级别
    auth_level = 2

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None
    # 站点助手实例
    sites: SitesHelper = None
    # 加载的站点处理器
    _site_schema: list = []

    # 配置属性
    _enabled: bool = False
    _cron: str = ""
    _onlyonce: bool = False
    _notify: bool = False
    _timeout: int = 15
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
            self._timeout = config.get("timeout", 15)

            # 保存配置
            self.__update_config()

        # 加载模块
        if self._enabled or self._onlyonce:
            self._site_schema = ModuleHelper.load('app.plugins.siteopencheck.sites',
                                                  filter_func=lambda _, obj: hasattr(obj, 'match'))
            logger.info(f"已加载 {len(self._site_schema)} 个站点注册处理器")

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
            "cron": self._cron,
            "timeout": self._timeout
        })

    def get_service(self) -> List[Dict[str, Any]]:
        """注册插件公共服务"""
        if self._enabled and self._cron:
            return [{
                "id": "SiteOpenCheck",
                "name": "站点开注检查 - 定时任务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__check_all_sites,
                "kwargs": {}
            }]
        return []

    def __get_all_sites(self) -> Dict[str, Any]:
        """获取过滤后的站点列表，保留 public=false 的站点"""
        all_sites = self.sites.get_indexsites()
        if not all_sites:
            logger.error("未获取到站点信息")
            return {}

        # 过滤站点，保留 public=false 的站点
        filtered_sites = {}
        for domain, site_info in all_sites.items():
            # 私有站点 & 没有其他域名
            if not site_info.get("public", True) and domain in site_info.get("url", ""):
                filtered_sites[domain] = site_info

        logger.info(f"获取到 {len(filtered_sites)} 个PT站点")
        return filtered_sites

    def __check_all_sites(self):
        """检查所有站点的开注状态"""
        logger.info("开始检查所有站点的开注状态")
        try:
            # 获取过滤后的站点
            all_sites = self.__get_all_sites()
            if not all_sites:
                logger.warning("没有需要检查的站点")
                return

            # 存储检查结果
            check_results = []
            open_sites = []
            closed_sites = []
            error_sites = []

            old_check_results = self.get_data('check_results') or []
            skip_sites = set()
            for s in old_check_results:
                if s.get('status') == 'error' or s.get('status') == 'unknown':
                    skip_sites.add(s.get("domain"))
                    check_results.append(s)
                    error_sites.append(s)

            # 遍历所有站点
            for domain, site_info in all_sites.items():
                try:
                    if domain in skip_sites:
                        logger.info(f"检测到上次访问该站点时出错，跳过站点 {domain} 的检查")
                        continue

                    site_name = site_info.get("name", domain)
                    site_url = site_info.get("url", f"https://{domain}")

                    # 检查站点开注状态（全部委托给处理器）
                    check_result = self.__check_site_registration(site_info)

                    # 直接使用处理器返回的结果，只添加必要的字段
                    result = check_result.copy()
                    result.update({
                        "domain": domain,
                        "name": site_name,
                        "url": site_url,
                        "check_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })
                    check_results.append(result)

                    status = result.get("status", "unknown")
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
            self.save_data('check_results', check_results)

            # 发送通知
            if self._notify:
                self.__send_notification(len(check_results), len(open_sites), len(closed_sites), len(error_sites))

            logger.info(
                f"站点开注检查完成，共检查 {len(check_results)} 个站点，开注 {len(open_sites)} 个，关闭 {len(closed_sites)} 个，异常 {len(error_sites)} 个")

        except Exception as e:
            logger.error(f"检查所有站点时发生错误: {str(e)}")

    def __check_site_registration(self, site_info: Dict[str, Any]) -> Dict[str, Any]:
        """检查单个站点的注册状态"""
        signup_url = ''
        try:
            # 使用处理器执行完整检测
            handler = self.__build_ins(site_info.get("url", ""))
            signup_url = handler.build_signup_url(site_info)
            status, message = handler.check(site_info)
            return {
                "status": status,
                "message": message,
                "signup_url": signup_url
            }
        except Exception as e:
            site_name = site_info.get("name", "unknown")
            logger.error(f"检查站点 {site_name} 注册状态时发生错误: {str(e)}")
            return {
                "status": "error",
                "message": f"检查失败: {str(e)}",
                "signup_url": signup_url
            }

    def __build_ins(self, url) -> Any:
        """构建站点处理器类"""
        final_schema = None
        for site_schema in self._site_schema:
            try:
                if site_schema.match(url):
                    logger.info(f"使用特定注册处理器处理站点: {url}")
                    final_schema = site_schema
                    break
            except Exception as e:
                logger.error("站点模块加载失败：%s" % str(e))

        if not final_schema:
            # 未匹配到则返回基础处理器
            from .sites.base import DefaultOpenCheckHandler
            final_schema = DefaultOpenCheckHandler

        ret_ins = final_schema()
        ret_ins.init(self._timeout, self._retry_interval)
        return ret_ins

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
            from .ui_components import SiteOpenCheckUIComponents

            # 获取检查结果
            check_results = self.get_data('check_results') or []

            # 创建顶部统计信息
            top_row = SiteOpenCheckUIComponents.create_top_stats(check_results)

            # 创建站点列表
            site_rows = []
            if check_results:
                site_rows = SiteOpenCheckUIComponents.create_site_list(check_results)

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
                            {'component': 'VAlert',
                             'props': {'type': 'error', 'variant': 'tonal', 'text': f'生成页面时发生错误: {str(e)}'}}
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
                                                    'sm': 4
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
                                                    'sm': 4
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
                                                    'sm': 4
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
                                                    'sm': 6
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
                                                    'sm': 6
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
            "cron": "0 9 * * *",
            "timeout": 15
        }
