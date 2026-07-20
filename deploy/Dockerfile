# 港电统一平台参考镜像(里程碑 10 部署包)
# 说明:内网离线部署优先——wheels 预下载后 COPY 安装;联网环境直接 pip。
# Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
FROM python:3.12-slim

RUN useradd --create-home --shell /usr/sbin/nologin gd
WORKDIR /app

COPY requirements.txt ./
# 离线:提前将 wheels/ 放入构建上下文;在线则回退 PyPI
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -d wheels ]; then pip install --no-index --find-links wheels -r requirements.txt; \
    else pip install -r requirements.txt; fi

COPY packages/ packages/
COPY apps/ apps/
COPY scripts/ scripts/
COPY selfcheck/ selfcheck/
COPY frontend/ frontend/

ENV PYTHONPATH=/app:/app/packages \
    CRYPTO_SUITE=intl
USER gd
# 各服务以 compose command 覆盖入口(run_idp/run_certvault/...)
CMD ["python3", "scripts/run_idp.py", "--host", "0.0.0.0", "--port", "9000"]
