from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event.filter import PlatformAdapterType
import asyncio
import aiohttp
import json
from datetime import datetime

@register("minecraft_monitor", "YourName", "Minecraft服务器监控插件，定时获取服务器状态", "1.0.0")
class MyPlugin(Star):
    DEFAULT_API_BASE_URL = "https://api.mcstatus.io/v2/status/"

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.task = None  # 用于存储定时任务
        
        # 从配置获取参数
        target_group_raw = self.config.get("target_group")
        self.target_group = None
        
        # 验证target_group是否为有效数字
        if target_group_raw is not None:
            target_group_str = str(target_group_raw).strip()
            if target_group_str.isdigit():
                self.target_group = target_group_str
            else:
                logger.error(f"配置中的 target_group '{target_group_raw}' 不是有效的数字，已忽略。")
        
        self.server_name = self.config.get("server_name", "Minecraft服务器")
        self.server_ip = self.config.get("server_ip")
        self.server_port = self.config.get("server_port")
        self.server_type = self.config.get("server_type", "bedrock")  # 服务器类型：bedrock或java
        self.api_base_url = str(
            self.config.get("api_base_url", self.config.get("api_url_template", self.DEFAULT_API_BASE_URL))
        ).strip()
        self.check_interval = self.config.get("check_interval", 10)
        self.enable_auto_monitor = self.config.get("enable_auto_monitor", False)

        # 配置为空时回退默认模板
        if not self.api_base_url:
            self.api_base_url = self.DEFAULT_API_BASE_URL
        
        # 状态缓存，用于检测变化
        self.last_player_ids = None  # 上次的玩家ID集合，None表示未初始化
        self.last_player_id_name_map = {}  # 上次的 id -> 玩家名称 映射
        self.last_status = None        # 上次的服务器状态
        self.last_update_time = None   # 上次更新时间
        
        # 检查必要的配置是否完整
        if not self.target_group or not self.server_ip or not self.server_port:
            logger.error("Minecraft监控插件配置不完整，缺少 target_group、server_ip 或 server_port，自动监控功能将不会启动。")
            logger.error("请在配置文件中设置以下参数: target_group, server_ip, server_port")
            self.enable_auto_monitor = False
        else:
            logger.info(f"Minecraft监控插件已加载 - 目标群: {self.target_group}, 服务器: {self.server_ip}:{self.server_port}, 类型: {self.server_type}")
        
        # 如果启用了自动监控且配置完整，延迟启动任务
        if self.enable_auto_monitor:
            asyncio.create_task(self._delayed_auto_start())
    
    async def _delayed_auto_start(self):
        """延迟自动启动监控任务"""
        await asyncio.sleep(5)  # 等待5秒让插件完全初始化
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.direct_hello_task())
            logger.info("🚀 自动启动服务器监控任务")

    def _extract_player_name(self, player):
        """从玩家条目中提取玩家名称。"""
        if isinstance(player, dict):
            name = player.get("name_clean") or player.get("name") or player.get("username")
            return str(name) if name not in (None, "") else ""

        if player not in (None, ""):
            return str(player)
        return ""

    def _extract_player_names(self, player_list):
        """
        从玩家列表中提取玩家名称列表
        
        Args:
            player_list: API返回的玩家列表
            
        Returns:
            list: 玩家名称列表
        """
        if not player_list or not isinstance(player_list, list):
            return []
        
        player_names = []
        for player in player_list:
            name = self._extract_player_name(player) or "未知玩家"
            player_names.append(name)
        return player_names

    def _is_fake_player_name(self, player_name):
        """判断玩家名是否为假人（Anonymous Player）。"""
        normalized_name = str(player_name).strip().lower()
        return normalized_name == "anonymous player" or normalized_name.startswith("anonymous player ")

    def _is_fake_player(self, player):
        """判断玩家条目是否为假人。"""
        return self._is_fake_player_name(self._extract_player_name(player))

    def _filter_real_players(self, player_list):
        """过滤出真人玩家列表。"""
        if not player_list or not isinstance(player_list, list):
            return []
        return [player for player in player_list if not self._is_fake_player(player)]

    def _count_fake_players(self, player_list):
        """统计玩家列表中的假人数。"""
        if not player_list or not isinstance(player_list, list):
            return 0

        return sum(1 for player in player_list if self._is_fake_player(player))

    def _extract_player_id(self, player):
        """从玩家数据中提取稳定唯一标识，优先使用官方 ID 字段"""
        if isinstance(player, dict):
            for key in ("id", "uuid", "uuid_raw", "xuid"):
                value = player.get(key)
                if value not in (None, ""):
                    return str(value)

            # 部分 API 不返回 ID，退化到名称作为标识
            name = player.get("name_clean") or player.get("name") or player.get("username")
            if name:
                return f"name:{name}"
            return None

        if player not in (None, ""):
            return f"name:{player}"
        return None

    def _extract_player_identity_map(self, player_list):
        """提取 id -> 显示名称 映射，用于 ID 级别变化检测"""
        if not player_list or not isinstance(player_list, list):
            return {}

        player_map = {}
        for player in player_list:
            player_id = self._extract_player_id(player)
            if not player_id or player_id in player_map:
                continue

            if isinstance(player, dict):
                display_name = (
                    player.get("name_clean")
                    or player.get("name")
                    or player.get("username")
                    or f"未知玩家({player_id[:8]})"
                )
            else:
                display_name = str(player)

            player_map[player_id] = str(display_name)

        return player_map

    def _build_status_api_url(self, base_url):
        """根据 base_url 构建完整的状态查询 URL。"""
        if base_url is None:
            return None

        normalized_base_url = str(base_url).strip()
        if not normalized_base_url:
            return None

        if (
            "{type}" in normalized_base_url
            or "{ip}" in normalized_base_url
            or "{port}" in normalized_base_url
        ):
            return (
                normalized_base_url
                .replace("{type}", str(self.server_type))
                .replace("{ip}", str(self.server_ip))
                .replace("{port}", str(self.server_port))
            )

        normalized_base_url = normalized_base_url.rstrip("/") + "/"
        return f"{normalized_base_url}{self.server_type}/{self.server_ip}:{self.server_port}"

    async def _request_server_status(self, session, api_url, source_name):
        """请求单个状态接口，成功返回解析后的服务器信息，失败返回 None。"""
        if not api_url:
            return None

        headers = {
            "User-Agent": "MinecraftServerMonitor/1.0 (AstrBot Plugin)"
        }
        request_timeout = aiohttp.ClientTimeout(total=10)

        try:
            logger.info(f"使用{source_name}查询: {api_url}")
            async with session.get(api_url, headers=headers, timeout=request_timeout) as response:
                if response.status != 200:
                    logger.warning(f"{source_name} 查询失败 (状态码: {response.status})")
                    return None

                try:
                    data = await response.json()
                    logger.debug(f"{source_name}返回数据: {json.dumps(data, ensure_ascii=False)[:500]}...")
                except json.JSONDecodeError:
                    logger.error(f"{source_name} 响应JSON解析失败: {await response.text()}")
                    return None

                return self._parse_server_data(data)
        except aiohttp.ClientError as e:
            logger.warning(f"{source_name} 网络请求失败: {e}")
            return None
        except asyncio.TimeoutError:
            logger.warning(f"{source_name} 请求超时")
            return None
        except Exception as e:
            logger.error(f"{source_name} 查询时发生未知错误: {e}")
            return None

    async def _fetch_server_data(self):
        """
        获取Minecraft服务器原始数据，使用mcstatus.io API
        
        Returns:
            dict: 包含服务器信息的字典，失败时返回None
        """
        # 检查配置完整性
        if not self.server_ip or not self.server_port:
            logger.error("服务器IP或端口未配置")
            return None

        primary_api_url = self._build_status_api_url(self.DEFAULT_API_BASE_URL)
        custom_api_url = self._build_status_api_url(self.api_base_url)
        use_custom_api = bool(custom_api_url and custom_api_url != primary_api_url)

        try:
            async with aiohttp.ClientSession() as session:
                primary_task = asyncio.create_task(
                    self._request_server_status(session, primary_api_url, "主接口(mcstatus.io)")
                )

                custom_task = None
                if use_custom_api:
                    custom_task = asyncio.create_task(
                        self._request_server_status(session, custom_api_url, "自定义接口")
                    )

                # 主接口优先：即使并发发起，也始终先看主接口结果
                primary_result = await primary_task
                if primary_result is not None:
                    if custom_task and not custom_task.done():
                        custom_task.cancel()
                        try:
                            await custom_task
                        except asyncio.CancelledError:
                            pass
                    return primary_result

                # 主接口失败后，回退到自定义接口
                if custom_task:
                    custom_result = await custom_task
                    if custom_result is not None:
                        logger.info("主接口查询失败，已回退到自定义接口结果")
                        return custom_result

                logger.warning("主接口与自定义接口查询均失败")
                return None
        except Exception as e:
            logger.error(f"获取服务器信息时发生未知错误: {e}")
            return None
    
    def _parse_server_data(self, data):
        """解析mcstatus.io API返回的数据"""
        # 检查服务器是否在线
        online = data.get('online', False)
        server_status = 'online' if online else 'offline'
        
        # 获取服务器名称
        server_name = data.get('hostname', self.server_name)
        if not server_name or server_name == '':
            server_name = f"{self.server_ip}:{self.server_port}"
        
        # 处理MOTD信息
        motd_info = data.get('motd', {})
        motd_clean = ""
        motd_raw = ""
        
        if isinstance(motd_info, dict):
            # mcstatus.io返回clean和raw两个版本
            motd_clean = motd_info.get('clean', '')
            motd_raw = motd_info.get('raw', '')
            
            # 如果没有clean版本，使用raw版本
            if not motd_clean and motd_raw:
                motd_clean = motd_raw
        elif isinstance(motd_info, str):
            motd_clean = motd_info
        
        # 获取版本信息
        version_info = data.get('version', {})
        if isinstance(version_info, dict):
            version = version_info.get('name', '未知版本')
            protocol = version_info.get('protocol', '未知')
        else:
            version = str(version_info) if version_info else '未知版本'
            protocol = '未知'
        
        # 获取玩家信息
        players_info = data.get('players', {})
        if isinstance(players_info, dict):
            total_online_players = players_info.get('online', 0)
            max_players = players_info.get('max', 0)
            
            # 玩家列表
            player_list = players_info.get('list', [])
        else:
            total_online_players = 0
            max_players = 0
            player_list = []

        fake_player_count = self._count_fake_players(player_list)
        real_player_list = self._filter_real_players(player_list)

        try:
            total_online_players = int(total_online_players)
        except (TypeError, ValueError):
            total_online_players = 0

        # 优先使用总人数-假人数，兜底使用真人列表长度，避免空列表导致计数异常
        real_online_players = max(total_online_players - fake_player_count, len(real_player_list), 0)
        
        # 获取服务器GUID/ID
        server_id = data.get('id', '未知')
        
        # 获取服务器端口（实际端口）
        actual_port = data.get('port', self.server_port)
        
        # 获取服务器图标（base64编码）
        server_icon = data.get('icon', '')
        
        # 获取服务器软件（如果有）
        server_software = data.get('software', '未知')
        
        # 获取服务器地图
        server_map = data.get('map', {}).get('name', '未知') if isinstance(data.get('map'), dict) else '未知'
        
        # 记录更新时间
        self.last_update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        return {
            'status': server_status,
            'name': server_name,
            'version': version,
            'online': real_online_players,
            'total_online': total_online_players,
            'fake_online': fake_player_count,
            'max': max_players,
            'players': real_player_list,
            'motd': motd_clean,
            'protocol': protocol,
            'id': server_id,
            'port': actual_port,
            'icon': server_icon,
            'software': server_software,
            'map': server_map,
            'update_time': self.last_update_time
        }

    def _format_server_info(self, server_data):
        """
        将服务器原始数据格式化为可读消息
        
        Args:
            server_data: 从_fetch_server_data获取的数据字典
            
        Returns:
            str: 格式化后的消息，失败时返回错误信息
        """
        if server_data is None:
            return "❌ 获取服务器数据失败"
        
        server_status = server_data['status']
        server_name = server_data['name']
        version = server_data['version']
        online_players = server_data['online']
        max_players = server_data['max']
        player_list = server_data['players']
        fake_player_count = server_data.get('fake_online')
        motd = server_data.get('motd', '')
        protocol = server_data.get('protocol')
        server_id = server_data.get('id')
        server_software = server_data.get('software')
        server_map = server_data.get('map')
        update_time = server_data.get('update_time', '未知')

        if fake_player_count is None:
            fake_player_count = self._count_fake_players(player_list)
        
        # 构建消息
        status_emoji = "🟢" if server_status == "online" else "🔴"
        message = f"{status_emoji} 服务器: {server_name}\n"
        
        # 添加MOTD信息
        if motd and motd != '':
            # 限制MOTD长度，避免消息过长
            if len(motd) > 120:
                motd = motd[:120] + "..."
            message += f"📝 MOTD: {motd}\n"
            
        # 添加服务器软件信息
        if server_software and server_software != '未知':
            message += f"🛠️ 软件: {server_software}\n"

        message += f"👥 在线玩家: {online_players}/{max_players}"
        message += f"\n🤖 在线假人: {fake_player_count}"

        # 处理玩家列表
        if online_players > 0:
            player_names = self._extract_player_names(player_list)
            if player_names:
                # 限制显示的玩家数量
                display_count = min(8, len(player_names))
                display_names = player_names[:display_count]
                message += f"\n📋 玩家列表: {', '.join(display_names)}"
                if len(player_names) > display_count:
                    message += f" (+{len(player_names) - display_count}人)"
            else:
                # 如果有玩家在线但无法获取列表，显示提示信息
                message += f"\n📋 当前有 {online_players} 名玩家在线"
        else:
            message += "\n📋 当前无真人玩家在线"
        
        # 添加服务器地图
        if server_map and server_map != '未知':
            message += f"\n🗺️ 地图: {server_map}"
        
        # 添加服务器ID/GUID
        if server_id and server_id != '未知':
            # 缩短ID显示，只显示前12位
            short_id = server_id[:12] + "..." if len(server_id) > 12 else server_id
            message += f"\n🆔 ID: {short_id}"
        
        # 添加服务器类型标识
        server_type_display = "基岩版" if self.server_type == "bedrock" else "Java版"
        message += f"\n🔧 类型: {server_type_display}"
        
        # 添加更新时间
        message += f"\n🕒 更新时间: {update_time}"
        
        return message

    async def get_minecraft_server_info(self, format_message=True):
        """
        获取Minecraft服务器信息
        
        Args:
            format_message: 是否格式化为消息字符串，False时返回原始数据字典
            
        Returns:
            str或dict: 格式化的消息或原始数据字典
        """
        server_data = await self._fetch_server_data()
        
        if not format_message:
            return server_data
        
        return self._format_server_info(server_data)
    
    def check_server_changes(self, server_data):
        """检查服务器状态是否有变化，返回是否需要发送消息和变化描述"""
        if server_data is None:
            return False, "获取服务器数据失败"
        
        current_players = server_data['players']
        current_status = server_data['status']

        # 使用玩家 ID 判断加入/离开，避免仅按总人数判断的误报
        current_player_map = self._extract_player_identity_map(current_players)
        current_player_ids = set(current_player_map.keys())

        # 检查是否是首次检查（使用 None 判断）
        if self.last_player_ids is None:
            # 首次检查，更新缓存但不发送消息（除非有玩家在线）
            self.last_player_ids = current_player_ids.copy()
            self.last_player_id_name_map = current_player_map.copy()
            self.last_status = current_status

            if current_player_ids:
                return True, "服务器监控已启动，当前有玩家在线"
            else:
                return True, "服务器监控已启动"

        # 检查变化
        changes = []
        
        # 检查服务器状态变化
        if self.last_status != current_status:
            if current_status == "online":
                changes.append(f"🟢 服务器已上线")
            else:
                changes.append(f"🔴 服务器已离线")

        # 检查玩家 ID 变化
        joined_player_ids = current_player_ids - self.last_player_ids
        if joined_player_ids:
            joined_player_names = [
                current_player_map.get(player_id, f"ID:{player_id[:8]}")
                for player_id in sorted(joined_player_ids)
            ]
            changes.append(f"📈 {', '.join(joined_player_names)} 加入了服务器 (+{len(joined_player_ids)})")

        left_player_ids = self.last_player_ids - current_player_ids
        if left_player_ids:
            left_player_names = [
                self.last_player_id_name_map.get(player_id, f"ID:{player_id[:8]}")
                for player_id in sorted(left_player_ids)
            ]
            changes.append(f"📉 {', '.join(left_player_names)} 离开了服务器 (-{len(left_player_ids)})")

        # 更新缓存
        self.last_player_ids = current_player_ids.copy()
        self.last_player_id_name_map = current_player_map.copy()
        self.last_status = current_status

        # 如果有变化，返回True和变化描述
        if changes:
            return True, "\n".join(changes)
        else:
            return False, "无变化"
    
    async def initialize(self):
        """插件初始化方法"""
        logger.info("Minecraft服务器监控插件已加载，使用 /start_hello 启动定时任务")
    
    async def notify_subscribers(self, message: str):
        """发送通知到目标群组"""
        if not self.target_group:
            logger.error("❌ 目标群号未配置，无法发送通知")
            return False
        
        try:
            # 获取AIOCQHTTP客户端并发送
            platform = self.context.get_platform(PlatformAdapterType.AIOCQHTTP)
            
            if not platform or not hasattr(platform, 'get_client'):
                logger.error("❌ 无法获取AIOCQHTTP客户端")
                return False
                
            client = platform.get_client()
            
            result = await client.api.call_action('send_group_msg', **{
                'group_id': int(self.target_group),
                'message': message
            })
            
            if result and result.get('message_id'):
                logger.info(f"✅ 已发送通知到群 {self.target_group}")
                return True
            else:
                logger.warning(f"❌ 发送失败: {result}")
                return False
        except Exception as e:
            logger.error(f"发送通知时出错: {e}")
            return False
    
    async def direct_hello_task(self):
        """定时获取并检测Minecraft服务器变化"""
        while True:
            try:
                # 等待配置的检查间隔
                await asyncio.sleep(self.check_interval)
                
                # 仅获取一次服务器原始数据
                server_data = await self._fetch_server_data()
                
                if server_data is None:
                    logger.warning("❌ 获取服务器数据失败，跳过本次检查")
                    continue
                
                # 检查是否有变化
                should_send, change_message = self.check_server_changes(server_data)
                
                if should_send:
                    # 有变化，发送消息
                    # 先发送变化提醒
                    change_notification = f"🔔 服务器状态变化：\n{change_message}"
                    
                    # 使用已获取的数据格式化完整状态（避免第二次网络请求）
                    full_status = self._format_server_info(server_data)
                    
                    # 构建最终消息
                    final_message = f"{change_notification}\n\n📊 当前状态：\n{full_status}"
                    
                    # 使用抽象的通知函数发送消息
                    await self.notify_subscribers(final_message)
                else:
                    # 无变化，仅记录日志
                    logger.info(f"🔍 服务器状态无变化: 玩家数 {server_data['online']}/{server_data['max']}")
                    
            except Exception as e:
                logger.error(f"定时监控任务出错: {e}")
                # 出错时等待一下再继续
                await asyncio.sleep(5)

    # 定时任务控制指令
    @filter.command("start_server_monitor")
    async def start_server_monitor_task(self, event: AstrMessageEvent):
        """启动服务器监控任务"""
        if self.task and not self.task.done():
            yield event.plain_result("服务器监控任务已经在运行中")
            return
        
        self.task = asyncio.create_task(self.direct_hello_task())
        logger.info("启动服务器监控任务")
        yield event.plain_result(f"✅ 服务器监控任务已启动，每{self.check_interval}秒检查一次服务器状态")
    
    @filter.command("stop_server_monitor")
    async def stop_server_monitor_task(self, event: AstrMessageEvent):
        """停止服务器监控任务"""
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("停止服务器监控任务")
            yield event.plain_result("✅ 服务器监控任务已停止")
        else:
            yield event.plain_result("❌ 监控任务未在运行")
    
    @filter.command("查询")
    async def get_server_status(self, event: AstrMessageEvent):
        """立即获取服务器状态"""
        server_info = await self.get_minecraft_server_info()
        yield event.plain_result(server_info)
    
    @filter.command("重置监控")
    async def reset_monitor(self, event: AstrMessageEvent):
        """重置监控状态缓存"""
        self.last_player_ids = None
        self.last_player_id_name_map = {}
        self.last_status = None
        logger.info("监控状态缓存已重置")
        yield event.plain_result("✅ 监控状态缓存已重置，下次检测将视为首次检测")
    
    async def terminate(self):
        """插件销毁方法"""
        # 停止定时任务
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("定时发送任务已停止")
