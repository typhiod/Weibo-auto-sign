# 微博超话批量签到脚本

自动化微博超话签到工具，支持多账户批量操作，支持 Cookie 过期自动续期。

## 功能特性

- ✅ 自动获取所有关注的超话列表
- ✅ 批量签到所有超话
- ✅ 支持多账户配置
- ✅ 智能防重复签到
- ✅ 详细的签到统计报告
- ✅ Cookie 过期自动检测 + Telegram 扫码续期
- ✅ Cookie 保活（定时访问微博首页）
- ✅ 支持 Docker 部署

## 部署方式

### 方式一：Docker 部署（推荐）

#### 1. 准备 Cookie 文件

首次使用需要手动获取一次 Cookie：

1. 登录微博网页版 (weibo.com)
2. 按 F12 打开开发者工具 → Network 标签
3. 刷新页面，复制任意请求的 Cookie
4. 保存到 `data/weibo.com_cookies.txt`

或使用扫码登录工具自动获取：
```bash
python login.py
```

#### 2. 配置

编辑 `docker-compose.yml`，填入你的 Telegram Bot Token 和 Chat ID：

```yaml
- TG_TOKEN=你的Telegram_Bot_Token
- TG_CHAT_ID=你的Telegram_Chat_ID
```

#### 3. 启动

```bash
mkdir -p data
# 将 cookie 文件放入 data/ 目录
docker compose up -d --build
```

#### 4. 验证

```bash
docker logs weibo-sign
```

#### Docker 定时任务说明

| 任务 | 时间 | 说明 |
|------|------|------|
| Cookie 检测 | 每天 22:00 | 检测过期 → 自动推送二维码到 Telegram |
| 超话签到 | 每天 00:00 | 批量签到所有关注的超话 |
| Cookie 保活 | 每 6 小时 | 访问微博首页，延长会话有效期 |

### 方式二：直接运行

```bash
pip install requests
python wb.py
```

### 方式三：青龙面板

1. 添加脚本到青龙面板
2. 配置环境变量 `WEIBO_COOKIE` 或 `WEIBO_COOKIES`
3. 设置定时任务：`0 8 * * *` (每天早上8点执行)

## Cookie 自动续期

配置了 Telegram Bot 后，脚本会在每天 22:00 自动检测 Cookie 状态：

1. **检查过期时间** — 解析 Cookie 文件中 ALF/SUB/SUBP 的过期时间戳
2. **API 验证** — 调用微博 API 确认 Cookie 是否被服务端吊销
3. **自动续期** — 如果即将过期或已失效，自动生成登录二维码推送到 Telegram
4. **扫码完成** — 用微博 APP 扫码确认后，新 Cookie 自动写入文件

默认提前 7 天触发续期，可通过环境变量调整：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `RENEW_AHEAD_DAYS` | `7` | 距过期几天内触发续期 |
| `QR_TIMEOUT` | `300` | 等待扫码超时秒数 |
| `CHECK_COOKIE_CRON` | `0 22 * * *` | Cookie 检测时间 |

## 环境变量配置

### Cookie 配置

**单账户：**
```bash
export WEIBO_COOKIE="你的cookie内容"
```

**多账户（支持三种分割方式）：**
```bash
# 方式1：使用 @ 分割
export WEIBO_COOKIES="cookie1@cookie2@cookie3"

# 方式2：使用换行分割
export WEIBO_COOKIES="cookie1
cookie2
cookie3"

# 方式3：使用 ---- 分割
export WEIBO_COOKIES="cookie1----cookie2----cookie3"
```

### 通知配置

支持 Telegram、Server酱、SMTP 邮件三种通知方式，详见 `docker-compose.yml` 中的注释。

## 输出示例

```
============================================================
🌟 微博超话批量签到脚本 v1.2
📅 支持多账户批量签到
============================================================
🔍 检测到多账户配置，共 2 个账户
🎯 开始执行批量签到任务，共 2 个账户
============================================================
[08:00:01] [i] [账户1] 🚀 开始执行签到任务 (用户12345678...)
[08:00:02] [i] [账户1] 📋 正在获取超话列表...
[08:00:03] [i] [账户1] 📊 成功获取到 15 个超话
[08:00:04] [+] [账户1] [超话名称] 签到成功
...
============================================================
🏆 全部账户签到完成！
✅ 总签到成功: 22 个超话
⏱️  总耗时: 45 秒
```

## 常见问题

**Q: Cookie 失效怎么办？**
A: 如果配置了 Telegram，脚本会自动检测并推送二维码，扫码即可续期。也可以手动运行 `python login.py` 重新获取。

**Q: 支持几个账户？**
A: 理论上无限制，但建议不超过 10 个账户。

**Q: 签到失败怎么办？**
A: 检查 Cookie 是否有效，网络连接是否正常。查看日志：`docker logs weibo-sign` 或 `logs/sign.log`。

## 项目结构

```
├── wb.py                 # 核心签到脚本
├── login.py              # 扫码登录工具
├── check_cookie.py       # Cookie 健康检测 + 自动续期
├── heartbeat.py          # Cookie 保活
├── entrypoint.sh         # Docker 入口
├── deploy.sh             # Linux 一键部署脚本
├── Dockerfile
├── docker-compose.yml
└── data/
    └── weibo.com_cookies.txt  # Cookie 文件（需自行创建）
```

## 许可证

[MIT License](LICENSE)

## 版本

v1.2
