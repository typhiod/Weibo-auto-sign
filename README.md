# 微博超话批量签到脚本

自动化微博超话签到工具，支持多账户批量操作，支持 Cookie 过期自动续期。

## 功能特性

- ✅ 自动获取所有关注的超话列表
- ✅ 批量签到所有超话
- ✅ 支持多账户配置
- ✅ 智能防重复签到
- ✅ 详细的签到统计报告
- ✅ Cookie 过期自动检测 + Telegram 扫码续期
- ✅ Cookie 保活（验证登录并保存服务器更新的 Cookie）
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
| 超话签到 | 每天 00:00、06:00 | 00:00 签到全部超话；06:00 只补签当天尚未成功的超话 |
| Cookie 保活 | 每 6 小时的第 5 分钟 | 验证登录，保存服务器实际更新的 Cookie |

#### 已部署项目覆盖更新

将 `WeiBo-cookie-fix.zip` 放到**原部署目录**，然后执行：

```bash
unzip -o WeiBo-cookie-fix.zip
bash redeploy.sh
```

更新包没有外层文件夹，直接覆盖项目代码。包中不包含真实 Cookie、`data/`、`logs/`、`.env` 或 `docker-compose.yml`，会保留现有 Telegram 配置和数据挂载。已有部署无需替换自己的 `docker-compose.yml`。

修改 Python 代码后必须重新构建镜像，单纯重启容器不会装入新代码。`redeploy.sh` 会沿用原配置重新构建 `weibo-sign` 服务。

重新部署后可以立即检查一次：

```bash
docker compose exec -T weibo-sign python -u heartbeat.py
docker compose exec -T weibo-sign tail -n 30 /app/logs/heartbeat.log
```

正常输出会说明“登录有效，Cookie 已保存”，并区分是否观察到关键凭证过期时间延长。若已有任务运行，会提示本次跳过；待该任务结束再检查即可。若 Cookie 确认已失效，可运行 `docker compose exec -T weibo-sign python -u check_cookie.py` 触发扫码。

直接使用 Python/crontab 部署时，覆盖解压后运行 `bash deploy.sh` 更新定时任务。

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

1. **验证并刷新** — 访问首页及需要登录的超话列表接口，确认状态后保存服务器更新的 Cookie
2. **检查真实过期时间** — 根据更新后的 ALF/SUB/SUBP 时间判断是否临近到期，不伪造有效期
3. **扫码续期** — 服务端明确要求登录，或验证后仍临近到期时推送二维码；超时、503、限流、未知响应不会触发扫码
4. **扫码完成** — 新 Cookie 验证通过才替换对应账户的文件，并执行该账户签到

Cookie 的实际有效期由微博服务端决定。定时访问只能接收服务器愿意提供的刷新，不能保证固定的一个月或三个月有效期。保活日志会明确区分“当前登录有效”和“观察到凭证过期时间延长”。

签到、保活、检测和手动扫码共用任务锁，遇到另一任务运行会跳过，避免旧会话覆盖刚更新的 Cookie。

默认提前 7 天触发续期，可通过环境变量调整：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `RENEW_AHEAD_DAYS` | `7` | 距过期几天内触发续期 |
| `QR_TIMEOUT` | `300` | 等待扫码超时秒数 |
| `CHECK_COOKIE_CRON` | `0 22 * * *` | Cookie 检测时间 |
| `HEARTBEAT_CRON` | `5 */6 * * *` | Cookie 保活时间 |

## 环境变量配置

### Cookie 配置

**单账户：**
```bash
export WEIBO_COOKIE="你的cookie内容"
```

环境变量也可以填写 Netscape Cookie 文件的路径，推荐使用挂载的文件：

```bash
export WEIBO_COOKIE="/app/data/weibo.com_cookies.txt"
```

直接填写 Cookie 内容时，刷新和扫码结果会保存在 `.cookie_cache/` 中，后续任务优先读取该原始配置对应的缓存。Docker 中缓存位于挂载的 `data/` 内；修改环境变量中的 Cookie 内容会使用新的缓存。多账户分别缓存、分别检测，扫码时请使用通知中指定的账户。手动更新第 2 个账户可执行 `python login.py --account 2`。

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
├── cookie_store.py       # Cookie 来源、原子保存、登录验证、任务锁
├── redeploy.sh           # 已部署 Docker 项目覆盖更新
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

## 离线回归测试

测试使用虚构 Cookie 和模拟 HTTP，不访问微博或 Telegram：

```bash
python -B -m unittest discover -s tests -v
```
