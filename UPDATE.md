# 已部署项目更新说明

将 `WeiBo-cookie-fix.zip` 上传到原项目目录，解压后执行：

```bash
unzip -o WeiBo-cookie-fix.zip
bash redeploy.sh
```

压缩包没有外层目录。请在原先存放 `Dockerfile` 和 `docker-compose.yml` 的目录解压。

本包保留你的现有配置：不包含 `docker-compose.yml`、`.env`、Cookie 文件、`data/`、`logs/` 或 Git 历史。已经部署时不需要替换现有配置。

重新部署脚本沿用现有 compose 配置，重新构建并启动 `weibo-sign`。原来的 Telegram 配置、签到时间、Cookie 和日志挂载继续使用。

部署后可以立即验证：

```bash
docker compose exec -T weibo-sign python -u heartbeat.py
```

正常输出会显示“登录有效，Cookie 已保存”。日志会分别说明是否观察到关键凭证过期时间延长。网络错误不会发起扫码，也不会覆盖旧 Cookie。如果提示有另一任务正在运行，等任务结束再执行即可。

如果已有 Cookie 确实失效，执行：

```bash
docker compose exec -T weibo-sign python -u check_cookie.py
```

使用 Telegram 收到的二维码重新登录。新凭证验证通过后会保存真实过期时间并立即签到。旧版本人为写入的一年有效期无法还原，本次更新不会修改旧文件中的日期来冒充续期。

直接使用 Python/crontab 部署时，覆盖解压后执行 `bash deploy.sh` 更新定时任务。此方式保留 Cookie 文件，使用系统 Python 运行更新后的脚本。

本次修复包括：Cookie 真实属性保存和原子写入、每个账户的保活和扫码、环境变量 Cookie 缓存、登录失效与网络错误的区分、并发任务锁，以及更新后的 Docker/cron 部署入口。
