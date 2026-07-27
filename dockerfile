FROM ghcr.io/gjb8114/clang-tidy-gjb8114:latest

ENV PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

RUN sed -i 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g; s|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || \
    sed -i 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g; s|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' /etc/apt/sources.list 2>/dev/null || true \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        python3-pip \
        netcat-openbsd \
        libxcb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
        libxcb-render-util0 libxcb-render0 libxcb-shape0 libxcb-shm0 \
        libxcb-sync1 libxcb-util1 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 \
        libxcb-glx0 libxcb-icccm4 \
        libxkbcommon0 libxkbcommon-x11-0 \
        libfontconfig1 libdbus-1-3 libglib2.0-0 \
        libgl1 \
        xvfb \
 && rm -rf /var/lib/apt/lists/*

# DCAB (DeepSITRServer Linux 版) 整体复制进镜像
COPY DeepSITRServer /opt/dcab
RUN chmod +x /opt/dcab/DeepSITRServer /opt/dcab/core/codetidy
# DCAB 携带了自己的 Qt lib，通过 LD_LIBRARY_PATH 加载
ENV LD_LIBRARY_PATH=/opt/dcab/lib:$LD_LIBRARY_PATH
ENV DISPLAY=:99

WORKDIR /app
COPY . /app
RUN pip3 install --no-cache-dir --break-system-packages \
        -i http://mirrors.aliyun.com/pypi/simple/ \
        --trusted-host mirrors.aliyun.com \
        -r /app/requirements.txt

RUN mkdir -p /app/local_workspaces /app/workspaces/_tasks /data/uniportal
RUN chmod +x /app/start.sh

ENTRYPOINT []
CMD ["/app/start.sh"]