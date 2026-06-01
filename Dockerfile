FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir requests

COPY wb.py heartbeat.py login.py check_cookie.py ./

# 时区
ENV TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends cron tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app/logs

COPY entrypoint.sh /
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
