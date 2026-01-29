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
        self.check_interval = self.config.get("check_interval", 10)
        self.enable_auto_monitor = self.config.get("enable_auto_monitor", False)
        
        # 状态缓存，用于检测变化
        self.last_player_count = None  # 上次的玩家数量，None表示未初始化
        self.last_player_list = []     # 上次的玩家列表
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
    
    async def get_hitokoto(self):
        """获取一言句子"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://v1.hitokoto.cn/?encode=text", timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        return (await response.text()).strip()
                    return None
        except Exception as e:
            logger.warning(f"获取一言失败: {e}")
            return None

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
            if isinstance(player, dict):
                # 提取玩家名
                name = player.get("name_clean") or player.get("name") or player.get("username") or "未知玩家"
                player_names.append(str(name))
            else:
                player_names.append(str(player))
        return player_names

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
        
        try:
            # 使用mcstatus.io API
            # 注意：mcstatus.io API需要正确的URL格式
            api_url = f"https://api.mcstatus.io/v2/status/{self.server_type}/{self.server_ip}:{self.server_port}"
            
            logger.info(f"使用mcstatus.io API查询: {api_url}")
            
            async with aiohttp.ClientSession() as session:
                # 添加User-Agent头以避免某些API限制
                headers = {
                    'User-Agent': 'MinecraftServerMonitor/1.0 (AstrBot Plugin)'
                }
                
                async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        try:
                            data = await response.json()
                            logger.debug(f"API返回数据: {json.dumps(data, ensure_ascii=False)[:500]}...")  # 只记录前500字符
                        except json.JSONDecodeError:
                            logger.error(f"API响应JSON解析失败: {await response.text()}")
                            return None
                        
                        return self._parse_server_data(data)
                    else:
                        logger.warning(f"获取服务器信息失败 (状态码: {response.status})")
                        return None
                        
        except aiohttp.ClientError as e:
            logger.error(f"网络请求失败: {e}")
            return None
        except asyncio.TimeoutError:
            logger.warning("请求超时")
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
            online_players = players_info.get('online', 0)
            max_players = players_info.get('max', 0)
            
            # 玩家列表
            player_list = players_info.get('list', [])
        else:
            online_players = 0
            max_players = 0
            player_list = []
        
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
            'online': online_players,
            'max': max_players,
            'players': player_list,
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
        motd = server_data.get('motd', '')
        protocol = server_data.get('protocol')
        server_id = server_data.get('id')
        server_software = server_data.get('software')
        server_map = server_data.get('map')
        update_time = server_data.get('update_time', '未知')
        
        # 构建消息
        status_emoji = "🟢" if server_status == "online" else "🔴"
        message = f"{status_emoji} 服务器: {server_name}\n"
        
        # 添加MOTD信息
        if motd and motd != '':
            # 限制MOTD长度，避免消息过长
            if len(motd) > 120:
                motd = motd[:120] + "..."
            message += f"📝 MOTD: {motd}\n"
            
        message += f"🎮 版本: {version}\n"
        
        # 添加协议版本
        if protocol and protocol != '未知':
            message += f"🔌 协议: {protocol}\n"
            
        # 添加服务器软件信息
        if server_software and server_software != '未知':
            message += f"🛠️ 软件: {server_software}\n"
            
        message += f"👥 在线玩家: {online_players}/{max_players}"
        
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
            message += "\n📋 当前无玩家在线"
        
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
        
        current_online = server_data['online']
        current_players = server_data['players']
        current_status = server_data['status']
        
        # 使用统一的玩家名称提取方法
        current_player_names = self._extract_player_names(current_players)
        
        # 检查是否是首次检查（使用 None 判断）
        if self.last_player_count is None:
            # 首次检查，更新缓存但不发送消息（除非有玩家在线）
            self.last_player_count = current_online
            self.last_player_list = current_player_names.copy()
            self.last_status = current_status
            
            if current_online > 0:
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
        
        # 检查玩家数量变化
        player_diff = current_online - self.last_player_count
        if player_diff > 0:
            # 有玩家加入
            new_players = set(current_player_names) - set(self.last_player_list)
            if new_players:
                changes.append(f"📈 {', '.join(new_players)} 加入了服务器 (+{player_diff})")
            else:
                changes.append(f"📈 有 {player_diff} 名玩家加入了服务器")
        elif player_diff < 0:
            # 有玩家离开
            left_players = set(self.last_player_list) - set(current_player_names)
            if left_players:
                changes.append(f"📉 {', '.join(left_players)} 离开了服务器 ({player_diff})")
            else:
                changes.append(f"📉 有 {abs(player_diff)} 名玩家离开了服务器")
        
        # 更新缓存
        self.last_player_count = current_online
        self.last_player_list = current_player_names.copy()
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
                    
                    # 获取一言句子
                    hitokoto = await self.get_hitokoto()
                    
                    # 构建最终消息
                    final_message = f"{change_notification}\n\n📊 当前状态：\n{full_status}"
                    if hitokoto:
                        final_message += f"\n\n💬 {hitokoto}"
                    
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
        
        # 获取一言句子
        hitokoto = await self.get_hitokoto()
        if hitokoto:
            server_info += f"\n\n💬 {hitokoto}"
        
        yield event.plain_result(server_info)
    
    @filter.command("重置监控")
    async def reset_monitor(self, event: AstrMessageEvent):
        """重置监控状态缓存"""
        self.last_player_count = None
        self.last_player_list = []
        self.last_status = None
        logger.info("监控状态缓存已重置")
        yield event.plain_result("✅ 监控状态缓存已重置，下次检测将视为首次检测")
    
    async def terminate(self):
        """插件销毁方法"""
        # 停止定时任务
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("定时发送任务已停止")