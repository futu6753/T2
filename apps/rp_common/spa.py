# -*- coding: utf-8 -*-
"""
@file    spa.py
@brief   F2 业务 SPA 统一静态托管(H11 §一):构建产物挂 /app,history 模式
         深链一律兜底回 index.html;响应统一附 CSP(default-src 'self',
         禁 inline/eval,H11 §四.4)与 X-Content-Type-Options。产物缺失时
         /app 返回 503 明示「前端未构建」,不影响 API 面。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os
import posixpath

from fastapi.responses import FileResponse, JSONResponse

#: F2 SPA 统一 CSP(H11 §四.4:同源、禁 inline script、禁 eval;
#: 图片允许 data: 以容纳构建期内联小图标)
SPA_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
           "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
           "object-src 'none'; frame-ancestors 'self'; base-uri 'self'")

_SEC_HEADERS = {
    "Content-Security-Policy": SPA_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def _safe_join(dist_dir: str, rel: str) -> str:
    """
    @brief  目录穿越安全拼接:规范化后必须仍落在 dist 内,否则返回空串
    @param  dist_dir 构建产物目录(绝对或相对)
    @param  rel      URL 中 /app/ 之后的相对路径
    @return 安全的绝对路径;越界/非法返回 ""
    """
    rel = posixpath.normpath(rel.lstrip("/"))
    if rel in ("", "."):
        rel = "index.html"
    if rel.startswith("..") or rel.startswith("/") or "\x00" in rel:
        return ""
    base = os.path.abspath(dist_dir)
    full = os.path.abspath(os.path.join(base, rel))
    if full != base and not full.startswith(base + os.sep):
        return ""
    return full


def mount_spa(app, dist_dir: str, base: str = "/app") -> None:
    """
    @brief  将 SPA 构建产物挂到 base 路径:
            ① GET {base} 与 GET {base}/ → index.html;
            ② GET {base}/{path} → 命中实体文件则回文件,否则 history 兜底
              回 index.html(路由深链刷新可用,H11 §一 F2);
            ③ dist 缺失 → 503 明示构建缺失(API 面不受影响)。
    @param  app      FastAPI 应用
    @param  dist_dir 构建产物目录(apps/<app>/web/dist)
    @param  base     挂载前缀(缺省 /app)
    """
    index_path = os.path.join(dist_dir, "index.html")

    def _index_or_503():
        """@brief index.html 或 503(前端未构建)"""
        if os.path.isfile(index_path):
            return FileResponse(index_path, headers=dict(_SEC_HEADERS))
        return JSONResponse(
            {"error": "前端构建产物缺失,请先执行 frontend 构建"
                      "(npm run build)后重启"},
            status_code=503)

    @app.get(base, include_in_schema=False)
    def spa_index():
        """@brief SPA 首页"""
        return _index_or_503()

    @app.get(base + "/{spa_path:path}", include_in_schema=False)
    def spa_assets(spa_path: str):
        """@brief 静态资产 + history 兜底"""
        full = _safe_join(dist_dir, spa_path)
        if full and os.path.isfile(full):
            return FileResponse(full, headers=dict(_SEC_HEADERS))
        return _index_or_503()


def healthz_extras(profile) -> dict:
    """
    @brief  /healthz 横切附加字段(H11 §二横切:运行模式 + 当前密码套件,
            供 SPA 顶部横幅与管理页徽标)。profile 未装配时返回空,
            保持既有断言零影响。
    @param  profile SecurityProfile 或 None
    @return 附加字段字典
    """
    if profile is None:
        return {}
    return {"mode": profile.mode,
            "crypto_suite": profile.crypto_suite_name}
