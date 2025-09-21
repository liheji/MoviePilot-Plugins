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
from app.db.site_oper import SiteOper
from app.helper.sites import SitesHelper
from app.helper.module import ModuleHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.types import EventType
from app.utils.http import RequestUtils
from app.utils.security import SecurityUtils

class MedalWallNew(_PluginBase):
    # 插件名称
    plugin_name = "勋章墙-新版"
    # 插件描述
    plugin_desc = "站点勋章购买提醒、统计、展示。\n基于 KoWming 的勋章墙改造而来，详情可参考 https://github.com/KoWming"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/liheji/MoviePilot-Plugins/main/icons/Medal.png"
    # 插件版本
    plugin_version = "1.0"
    # 插件作者
    plugin_author = "liheji"
    # 作者主页
    author_url = "https://github.com/liheji"
    # 插件配置项ID前缀
    plugin_config_prefix = "medalwallnew_"
    # 加载顺序
    plugin_order = 10
    # 可使用的用户级别
    auth_level = 2

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None
    # 加载的模块
    _site_schema: list = []
    # 站点助手实例
    sites: SitesHelper = None
    siteoper: SiteOper = None

    # 配置属性
    _enabled: bool = False
    _cron: str = ""
    _onlyonce: bool = False
    _notify: bool = False
    _chat_sites: list = []
    _use_proxy: bool = True
    _timeout: int = 15
    _retry_times: int = 3
    _retry_interval: int = 5

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        # 停止现有任务
        self.stop_service()
        
        # 初始化助手
        self.sites = SitesHelper()
        self.siteoper = SiteOper()

        # 配置
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce")
            self._notify = config.get("notify")
            self._chat_sites = config.get("chat_sites") or []
            self._use_proxy = config.get("use_proxy", True)
            self._timeout = config.get("timeout", 15)
            self._retry_times = config.get("retry_times", 3)
            self._retry_interval = config.get("retry_interval", 5)

            # 过滤掉已删除的站点
            all_sites = [site.id for site in self.siteoper.list_order_by_pri()] + [site.get("id") for site in self.__custom_sites()]
            self._chat_sites = [site_id for site_id in all_sites if site_id in self._chat_sites]
            
            # 保存配置
            self.__update_config()

        # 加载模块
        if self._enabled or self._onlyonce:
            self._site_schema = ModuleHelper.load('app.plugins.medalwallnew.sites',
                                                  filter_func=lambda _, obj: hasattr(obj, 'match'))
            logger.info(f"已加载 {len(self._site_schema)} 个站点处理器")

            # 立即运行一次
            if self._onlyonce:
                # 定时服务
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                logger.info("勋章墙服务启动，立即运行一次")
                self._scheduler.add_job(func=self.__process_all_sites, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="勋章墙")

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
            "use_proxy": self._use_proxy,
            "chat_sites": self._chat_sites,
            "cron": self._cron,
            "retry_times": self._retry_times,
            "retry_interval": self._retry_interval,
            "timeout": self._timeout
        })

    def get_service(self) -> List[Dict[str, Any]]:
        """注册插件公共服务"""
        if self._enabled and self._cron:
            return [{
                "id": "MedalWallNew",
                "name": "勋章墙 - 定时任务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__process_all_sites,
                "kwargs": {}
            }]
        return []

    def __process_all_sites(self):
        """处理所有选中的站点"""
        logger.info("开始处理所有站点的勋章数据")
        try:
            if not self._chat_sites:
                logger.error("未选择站点")
                return

            # 存储所有可购买的勋章
            all_buy_medals = []
            # 存储需要推送的勋章
            notify_medals = []

            # 遍历所有选中的站点
            for site_id in self._chat_sites:
                try:
                    # 获取站点勋章数据
                    medals = self.get_medal_data(site_id)
                    if not medals:
                        continue

                    # 获取站点信息
                    site = self.siteoper.get(site_id)
                    if not site:
                        continue

                    # 筛选可购买的勋章
                    buy_medals = []
                    for medal in medals:
                        if self.is_current_time_in_range(medal.get('saleBeginTime', ''), medal.get('saleEndTime', '')):
                            buy_medals.append(medal)

                    if buy_medals:
                        all_buy_medals.extend(buy_medals)
                        # 只将可购买的勋章加入推送列表
                        notify_medals.extend([m for m in buy_medals if (m.get('purchase_status') or '').strip() in ['购买', '赠送']])

                except Exception as e:
                    logger.error(f"处理站点 {site_id} 时发生错误: {str(e)}")
                    continue

            # 发送通知 - 只推送可购买的勋章
            if self._notify and notify_medals:
                self.__send_notification(notify_medals)

            # 保存所有勋章数据
            self.save_data('medals', all_buy_medals, 'zmmedal')

        except Exception as e:
            logger.error(f"处理所有站点时发生错误: {str(e)}")

    def get_medal_data(self, site_id: str) -> List[Dict]:
        """统一入口：获取站点勋章数据"""
        try:
            # 获取站点信息
            site = self.siteoper.get(site_id)
            if not site:
                logger.error(f"未找到站点信息: {site_id}")
                return []

            # 获取适配的处理器
            handler = self.__build_class(site.url)
            if not handler:
                logger.error(f"未找到适配的站点处理器: {site.name}")
                return []

            # 获取勋章数据
            medals = handler().fetch_medals(site)

            # 保存数据到缓存
            self.save_data(f'medals_{site_id}', medals, 'zmmedal')

            return medals

        except Exception as e:
            logger.error(f"获取勋章数据失败: {str(e)}")
            return []

    def __build_class(self, url) -> Any:
        """构建站点处理器类"""
        # 首先尝试匹配特定的站点处理器
        for site_schema in self._site_schema:
            try:
                if site_schema.match(url):
                    logger.info(f"使用特定处理器处理站点: {url}")
                    return site_schema
            except Exception as e:
                logger.error("站点模块加载失败：%s" % str(e))
        
        # 如果没有匹配到特定处理器，使用兜底的 PHP 处理器
        logger.info(f"未找到特定处理器，使用兜底 PHP 处理器处理站点: {url}")
        from .sites.base import PhpMedalHandler
        return PhpMedalHandler

    def __send_notification(self, notify_medals: List[Dict]):
        """发送通知消息"""
        # 按站点分组
        site_medals = {}
        for medal in notify_medals:
            site = medal.get('site', '')
            if site not in site_medals:
                site_medals[site] = []
            site_medals[site].append(medal)

        # 生成报告
        text_message = ""
        for site, medals in site_medals.items():
            # 站点分隔线
            text_message += "  ──────────\n"
            # 站点名称
            text_message += f"🌐 站点：{site}\n"
            # 该站点的所有勋章
            for medal in medals:
                # 勋章名称和价格
                text_message += f"《{medal.get('name', '')}》──价格: {medal.get('price', 0):,}\n"
                # 魔力加成
                text_message += f" 魔力加成：{medal.get('bonus_rate')}\n"
                # 购买时间
                begin_time = self.__format_time(medal.get('saleBeginTime', '不限'))
                end_time = self.__format_time(medal.get('saleEndTime', '不限'))
                text_message += f" 购买时间：{begin_time}~{end_time}\n"
                text_message += " \n"

        # 添加推送时间
        text_message += "──────────\n"
        text_message += f"⏰推送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"

        self.post_message(
            mtype=NotificationType.SiteMessage,
            title="【🎯 勋章墙】可购买勋章提醒：",
            text=text_message)

    def is_current_time_in_range(self, start_time, end_time):
        """判断当前时间是否在给定的时间范围内"""
        try:
            # 处理None值的情况
            if start_time is None or end_time is None:
                return False

            # 处理空字符串的情况
            if not start_time.strip() or not end_time.strip():
                return False

            # 处理"不限"的情况
            if "不限" in start_time or "不限" in end_time:
                return True

            # 处理包含"~"的情况
            if "~" in start_time:
                start_time = start_time.split("~")[0].strip()
            if "~" in end_time:
                end_time = end_time.split("~")[0].strip()

            # 尝试解析时间
            current_time = datetime.now()
            start_datetime = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
            end_datetime = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
            return start_datetime <= current_time <= end_datetime
        except Exception as e:
            logger.error(f"解析时间范围时发生错误: {e}")
            return False

    def __custom_sites(self) -> list:
        """获取自定义站点列表"""
        custom_sites = []
        custom_sites_config = self.get_config("CustomSites")
        if custom_sites_config and custom_sites_config.get("enabled"):
            custom_sites = custom_sites_config.get("sites", [])
        return custom_sites

    def __format_time(self, time_str: str) -> str:
        """格式化时间字符串，只保留日期部分"""
        if not time_str or time_str == '不限':
            return time_str
        try:
            # 尝试不同的时间格式
            formats = [
                "%Y-%m-%d %H:%M:%S",  # 标准格式
                "%Y-%m-%d",           # 只有日期
                "%Y/%m/%d %H:%M:%S",  # 斜杠分隔
                "%Y/%m/%d"            # 斜杠分隔只有日期
            ]

            for fmt in formats:
                try:
                    dt = datetime.strptime(time_str, fmt)
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue

            # 如果所有格式都不匹配，尝试直接提取日期部分
            if " " in time_str:
                return time_str.split(" ")[0]

            return time_str
        except Exception as e:
            logger.error(f"格式化时间出错: {str(e)}, 时间字符串: {time_str}")
            return time_str

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

    @eventmanager.register(EventType.SiteDeleted)
    def site_deleted(self, event):
        """删除对应站点选中"""
        site_id = event.event_data.get("site_id")
        config = self.get_config()
        if config:
            self._chat_sites = self.__remove_site_id(config.get("chat_sites") or [], site_id)
            # 保存配置
            self.__update_config()

    def __remove_site_id(self, do_sites, site_id):
        """移除站点ID"""
        if do_sites:
            if isinstance(do_sites, str):
                do_sites = [do_sites]
            # 删除对应站点
            if site_id:
                do_sites = [site for site in do_sites if int(site) != int(site_id)]
            else:
                # 清空
                do_sites = []
            # 若无站点，则停止
            if len(do_sites) == 0:
                self._enabled = False
        return do_sites

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        [{
            "path": "/xx",
            "endpoint": self.xxx,
            "methods": ["GET", "POST"],
            "summary": "API说明"
        }]
        """
        return [{
            "path": "/start_medalwallnew_process",
            "endpoint": self.__process_all_sites,
            "methods": ["GET"],
            "summary": "开始获取站点勋章",
            "description": "获取站点勋章",
        }]

    def get_page(self) -> list:
        """获取勋章页面数据"""
        try:
            from .ui_components import MedalUIComponents
            
            # 1. 汇总全局统计数据
            site_ids = self._chat_sites
            all_medals = []
            site_medal_map = {}
            site_name_map = {}
            
            for site_id in site_ids:
                medals = self.get_data(f'medals_{site_id}', 'zmmedal') or []
                unhas_medals = self.get_data(f'unhas_medals_{site_id}', 'zmmedal') or []
                has_medals = self.get_data(f'has_medals_{site_id}', 'zmmedal') or []
                
                # 合并去重
                site_medals = []
                processed = set()
                for medal_list in [medals, unhas_medals, has_medals]:
                    for medal in medal_list:
                        key = f"{medal.get('name')}|{medal.get('site')}"
                        if key not in processed:
                            processed.add(key)
                            site_medals.append(medal)
                            all_medals.append(medal)
                site_medal_map[site_id] = site_medals
                
                # 获取站点名
                site = self.siteoper.get(site_id)
                site_name_map[site_id] = site.name if site else f"站点{site_id}"

            # 全局统计
            site_count = len(site_ids)
            medal_total = len(all_medals)
            buy_count = sum(1 for m in all_medals if (m.get('purchase_status') or '').strip() in ['购买', '赠送'])
            owned_count = sum(1 for m in all_medals if (m.get('purchase_status') or '').strip() in ['已经购买', '已拥有'])
            not_buy_count = sum(1 for m in all_medals if (m.get('purchase_status') or '').strip() in ['已过可购买时间', '未到可购买时间', '需要更多工分', '需要更多魔力值', '需要更多蝌蚪', '库存不足', '仅授予'])
            unknown_count = sum(1 for m in all_medals if not (m.get('purchase_status') or '').strip())

            # 2. 顶部统计信息
            top_row = MedalUIComponents.create_top_stats(
                site_count, medal_total, buy_count, owned_count, not_buy_count, unknown_count
            )

            # 3. 站点分组
            site_rows = []
            for site_id in site_ids:
                medals = site_medal_map[site_id]
                site_name = site_name_map[site_id]
                site_section = MedalUIComponents.create_site_section(site_id, site_name, medals)
                site_rows.append(site_section)

            # 4. 页面结构
            return [top_row] + site_rows
            
        except Exception as e:
            logger.error(f"生成勋章页面时发生错误: {str(e)}")
            return [{
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {'cols': 12, 'md': 12},
                        'content': [
                            {'component': 'VAlert', 'props': {'type': 'error', 'variant': 'tonal', 'text': f'生成勋章页面时发生错误: {str(e)}'}}
                        ]
                    }
                ]
            }]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """拼装插件配置页面"""
        # 动态判断MoviePilot版本，决定定时任务输入框组件类型
        version = getattr(settings, "VERSION_FLAG", "v1")
        cron_field_component = "VCronField" if version == "v2" else "VTextField"
        
        # 需要过滤没有勋章的站点名称列表
        filtered_sites = ['星空', '高清杜比', '聆音', '朱雀', '馒头', '家园', '朋友', '我堡', '彩虹岛', '天空', '听听歌']
        # 获取站点列表并过滤
        all_sites = [site for site in self.sites.get_indexers() if not site.get("public") and site.get("name") not in filtered_sites] + self.__custom_sites()
        # 构建站点选项
        site_options = [{"title": site.get("name"), "value": site.get("id")} for site in all_sites]
        
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
                                                            'model': 'use_proxy',
                                                            'label': '启用代理',
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
                                                'text': 'mdi-web'
                                            },
                                            {
                                                'component': 'span',
                                                'text': '站点设置'
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
                                                    'cols': 12
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSelect',
                                                        'props': {
                                                            'chips': True,
                                                            'multiple': True,
                                                            'model': 'chat_sites',
                                                            'label': '选择站点',
                                                            'items': site_options,
                                                            'variant': 'outlined',
                                                            'color': 'primary',
                                                            'hide-details': True
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
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
                                                            'model': 'retry_interval',
                                                            'label': '重试间隔(秒)',
                                                            'items': [
                                                                {'title': '5秒', 'value': 5},
                                                                {'title': '10秒', 'value': 10},
                                                                {'title': '15秒', 'value': 15}
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
            "use_proxy": True,
            "chat_sites": [],
            "cron": "0 9 * * *",
            "retry_times": 1,
            "retry_interval": 5
        }
