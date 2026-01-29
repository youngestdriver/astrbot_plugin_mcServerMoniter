# Minecraft服务器监控插件

这是一个基于AstrBot框架开发的Minecraft服务器监控插件，可以定时检测服务器状态变化并发送通知。作者使用的简幻欢服务器不支持建站，因此使用AI制作了此工具，使用api工具查看服务器状态 大部分代码都由ai完成，我自己本人基本够用了，所以即使存在很多问题也并没有去修正，而且我是菜鸟修正这些代码对我来说还是太难了。作者的qq：1002887282

## 功能特性

- 🔄 **智能监控**: 定时检测服务器状态，只在有变化时发送通知
- 📊 **详细信息**: 显示在线玩家数量、版本信息、玩家列表等
- ⚡ **实时查询**: 支持即时查询服务器当前状态
- ⚙️ **灵活配置**: 支持通过WebUI配置目标群、服务器地址、检查间隔等
- 🚀 **自动启动**: 可配置插件加载时自动启动监控（延迟5秒启动）
- 💬 **一言集成**: 消息推送时附带一言句子，更有趣味性

## 配置选项

通过AstrBot的WebUI可以配置以下参数：

| 配置项 | 说明 | 类型 | 默认值 | 必填 |
|--------|------|------|--------|------|
| `target_group` | QQ群号 | 字符串/数字 | 无 | ✅ |
| `server_name` | 服务器名称 | 字符串 | "Minecraft服务器" | ❌ |
| `server_ip` | 服务器IP地址 | 字符串 | 无 | ✅ |
| `server_port` | 服务器端口 | 数字 | 无 | ✅ |
| `check_interval` | 监控检查间隔（秒） | 数字 | 10 | ❌ |
| `enable_auto_monitor` | 插件加载时自动启动监控 | 布尔值 | false | ❌ |

⚠️ **重要提示**：`target_group`、`server_ip`、`server_port` 为必填项，如果缺少这些配置，自动监控功能将不会启动。

## 可用指令

### 监控控制指令
- `/start_server_monitor` - 启动定时监控任务
- `/stop_server_monitor` - 停止监控任务
- `/reset_monitor` - 重置监控状态缓存（清除首次检测标记）

### 查询指令
- `/查询` - 立即查询服务器当前状态（附带一言句子）

## 监控逻辑

### 智能变化检测
插件使用状态缓存机制，只在以下情况发生时推送通知：

1. **首次启动监控**
   - 如果有玩家在线，发送"服务器监控已启动，当前有玩家在线"
   - 如果无玩家在线，发送"服务器监控已启动"

2. **玩家加入服务器**
   - 显示具体加入的玩家名称
   - 格式：`� [玩家名] 加入了服务器 (+1)`

3. **玩家离开服务器**
   - 显示具体离开的玩家名称
   - 格式：`📉 [玩家名] 离开了服务器 (-1)`

### 通知消息格式
当检测到变化时，将发送包含以下内容的消息：

```
🔔 服务器状态变化：
[变化描述]

📊 当前状态：
[服务器详细信息]

💬 [一言句子]
```

### 状态缓存
- `last_player_count`: 上次的玩家数量（None表示未初始化）
- `last_player_list`: 上次的玩家列表
- `last_status`: 上次的服务器状态

使用 `/reset_monitor` 可以清除缓存，下次检测将视为首次检测。

## 技术特性

- **异步架构**: 基于 `asyncio` 和 `aiohttp`，性能优异
- **API集成**: 使用 mcstatus.io API (`https://api.mcstatus.io/v2/status`) 获取服务器状态
- **直接推送**: 通过 AIOCQHTTP 客户端的 `send_group_msg` 接口直接发送群消息
- **错误处理**: 完整的异常处理和超时机制（API请求超时10秒）
- **日志系统**: 详细的日志记录，便于调试和监控
- **状态管理**: 智能的状态缓存机制，避免重复通知

## 安装使用

### 1. 安装插件
将插件文件放入 AstrBot 插件目录：
```
data/plugins/服务器查询/
├── main.py             # 主程序文件
├── metadata.yaml       # 插件元数据
├── _conf_schema.json   # 配置模式文件
├── LICENSE             # 许可证文件
└── README.md           # 说明文档
```

### 2. 配置插件
在 AstrBot WebUI 的插件配置页面设置以下参数：
- **target_group**: 你的QQ群号
- **server_ip**: Minecraft服务器IP
- **server_port**: 服务器端口（Java版默认25565）
- **server_name**: 自定义服务器名称（可选）
- **check_interval**: 检查间隔，建议10-60秒
- **enable_auto_monitor**: 是否自动启动监控

### 3. 启动监控
方式一：在配置中启用 `enable_auto_monitor`，插件加载后自动启动（延迟5秒）

方式二：手动在群内发送 `/start_server_monitor` 指令启动

## API说明

### mcstatus.io API
**请求URL**: `https://api.mcstatus.io/v2/status/{type}/{ip}:{port}`

其中 `{type}` 为 `java` 或 `bedrock`。

**返回数据格式**:
```json
{
  "online": true,
  "hostname": "Server Name",
  "version": {
    "name": "1.20.1",
    "protocol": 763
  },
  "players": {
    "online": 2,
    "max": 20,
    "list": [
      {"name": "Player1", "name_clean": "Player1"},
      {"name": "Player2", "name_clean": "Player2"}
    ]
  },
  "motd": {
    "clean": "Welcome to the server",
    "raw": "§aWelcome to the server"
  }
}
```

### 一言API
**请求URL**: `https://v1.hitokoto.cn/?encode=text`

返回纯文本格式的随机句子。

## 注意事项

- ⚠️ 插件需要网络连接以访问外部API
- ⚠️ 确保配置的QQ群号正确且机器人已加入该群
- ⚠️ 建议检查间隔不要设置过短（推荐10-60秒），避免频繁API请求
- ⚠️ 如果配置不完整，插件会记录错误日志但不会崩溃
- ⚠️ 插件启动时会延迟5秒启动监控任务，确保插件完全初始化

## 常见问题

**Q: 为什么自动监控没有启动？**  
A: 检查配置文件中 `target_group`、`server_ip`、`server_port` 是否都已配置，查看日志是否有错误提示。

**Q: 如何清除监控状态？**  
A: 使用 `/reset_monitor` 指令重置状态缓存。

**Q: 消息没有发送到群里？**  
A: 检查群号是否正确配置在WebUI配置页面，机器人是否有发送权限。

**Q: 如何更改监控的群？**  
A: 在WebUI配置页面修改 `target_group` 参数后重启插件。

## 更新日志

### v1.0.0 (2025-11-16)
- ✅ 基础监控功能
- ✅ 智能变化检测（玩家加入/离开）
- ✅ 定时任务系统
- ✅ 一言句子集成
- ✅ 完整的配置系统
- ✅ 自动启动支持
- ✅ 状态缓存机制
- ✅ 直接AIOCQHTTP推送
- ✅ 完整的错误处理

