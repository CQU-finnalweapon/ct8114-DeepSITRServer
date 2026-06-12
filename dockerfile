FROM ghcr.io/gjb8114/clang-tidy-gjb8114:latest

ENV PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3-pip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip3 config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
 && pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

# 预建 UniPortal 双源接入需要的目录, 避免容器首次启动时因目录不存在报错
# (实际数据由命名卷挂载到这两个路径之上, 这里仅保证挂载点存在)
RUN mkdir -p /app/local_workspaces /app/workspaces/_tasks /data/uniportal

ENTRYPOINT []
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]