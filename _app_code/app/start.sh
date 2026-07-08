#!/bin/sh
# 启动 DeepSITRServer（DCAB），再启动分析工�?uvicorn

chmod +x /opt/dcab/DeepSITRServer /opt/dcab/core/codetidy 2>/dev/null || true
SYSTEM_INCLUDE_PATHS=""
for include_dir in \
    /usr/lib/llvm-19/lib/clang/19/include \
    /usr/lib/gcc/x86_64-linux-gnu/13/include \
    /usr/include \
    /usr/include/x86_64-linux-gnu
do
    if [ -d "$include_dir" ]; then
        if [ -z "$SYSTEM_INCLUDE_PATHS" ]; then
            SYSTEM_INCLUDE_PATHS="$include_dir"
        else
            SYSTEM_INCLUDE_PATHS="$SYSTEM_INCLUDE_PATHS:$include_dir"
        fi
    fi
done

if [ -n "$SYSTEM_INCLUDE_PATHS" ]; then
    export CPATH="$SYSTEM_INCLUDE_PATHS${CPATH:+:$CPATH}"
    export C_INCLUDE_PATH="$SYSTEM_INCLUDE_PATHS${C_INCLUDE_PATH:+:$C_INCLUDE_PATH}"
    export CPLUS_INCLUDE_PATH="$SYSTEM_INCLUDE_PATHS${CPLUS_INCLUDE_PATH:+:$CPLUS_INCLUDE_PATH}"
fi

# 启动虚拟 X 显示，供 DCAB �?Qt xcb 平台插件使用
if command -v pkill >/dev/null 2>&1; then
    pkill -f "Xvfb :99" 2>/dev/null || true
fi
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
Xvfb :99 -screen 0 1x1x8 &
export DISPLAY=:99
sleep 1

cd /opt/dcab && ./DeepSITRServer &
DCAB_PID=$!

# 等待 DCAB 监听 8080
i=0
while [ $i -lt 30 ]; do
    if nc -z 127.0.0.1 8080 2>/dev/null; then
        echo "DCAB ready (pid=$DCAB_PID)"
        break
    fi
    sleep 1
    i=$((i + 1))
done

exec uvicorn server:app --host 0.0.0.0 --port 8000
