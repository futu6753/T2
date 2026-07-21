# 港电统一平台参考镜像(里程碑 10 部署包 · 单机六系统共用)
# 国内网络:构建参数 PIP_INDEX/APT_MIRROR 默认走国内源;离线环境优先用 wheels/。
# 六系统同一镜像,由 compose command 区分入口(run_idp/run_rp/run_adapter)。
# Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
FROM python:3.12-slim

# 国内源(可用 --build-arg 覆盖为内网制品库)
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
ARG APT_MIRROR=mirrors.aliyun.com

RUN useradd --create-home --shell /usr/sbin/nologin gd
WORKDIR /app

# opencv-python-headless 运行需的系统库(libGL/glib);换国内 apt 源加速
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
      /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# 离线:提前将 wheels/ 放入构建上下文;在线走国内 PyPI 镜像
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -d wheels ]; then \
      pip install --no-index --find-links wheels -r requirements.txt; \
    else \
      pip install -i "${PIP_INDEX}" -r requirements.txt; \
    fi

# 仅拷运行所需目录;前端只要构建产物(dist/vendor),不带源码与 node_modules
COPY packages/ packages/
COPY apps/ apps/
COPY scripts/ scripts/
COPY selfcheck/ selfcheck/

ENV PYTHONPATH=/app:/app/packages \
    CRYPTO_SUITE=intl
USER gd
CMD ["python3", "scripts/run_idp.py", "--host", "0.0.0.0", "--port", "9000"]
