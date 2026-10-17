FROM python:3.12-slim

WORKDIR /app

# 时区（运行时可用 TZ 覆盖，由 entrypoint.sh 生效）
ENV TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends cron tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir requests==2.32.3

RUN mkdir -p /app/logs

COPY entrypoint.sh /
RUN chmod +x /entrypoint.sh

# 代码放在最后，改代码时不必重新安装系统依赖
COPY wb.py heartbeat.py login.py check_cookie.py cookie_store.py ./

ENTRYPOINT ["/entrypoint.sh"]
