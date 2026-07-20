# -*- coding: utf-8 -*-
"""
@file    records.py
@brief   备案台账服务(02-B1):add_issuance 全量参数快照 + 成品存档;
         独立备案(不可溯源、候选自动剔除);13-R-CV-5 备案撤销(溯源仍命中
         但明示作废)与 48bit ID 空间用量;发证笔记(密文,仅发证人/管理员,
         越权 403 由路由层执行);13-R-CV-2 engine_feedback 回流。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
from datetime import datetime, timezone

from gd_common.errors import PolicyValidationError
from apps.certvault.wm.payload import TRACER_MAX


def _now() -> str:
    """@brief UTC ISO 时间"""
    return datetime.now(timezone.utc).isoformat()


RECORD_COLUMNS = ("id, tracer_id, engine, cert_id, cert_label, cert_type,"
                  " issuer_id, recipient, purpose, validity, visible_text,"
                  " params_json, engine_meta_json, wm_bit_len, wm_strength,"
                  " embed_w, embed_h, distort_seed, archive_path,"
                  " is_standalone, revoked_at, revoked_by, created_at")


def _row_to_record(row: tuple) -> dict:
    """@brief 行 → 备案字典(engine_meta 解 JSON)"""
    keys = [key.strip() for key in RECORD_COLUMNS.split(",")]
    record = dict(zip(keys, row))
    record["engine_meta"] = json.loads(record.pop("engine_meta_json") or "{}")
    record["params"] = json.loads(record.pop("params_json") or "{}")
    return record


class RecordService:
    """备案台账(发证人可见,管理员全量)。"""

    def __init__(self, db, store):
        """@brief 注入库与证件存储(存档 blob 复用 CertStore 落盘)"""
        self._db = db
        self._store = store

    # ---- 备案写入 -------------------------------------------------------
    def add_issuance(self, tracer_id: int, engine: str, cert: dict,
                     issuer_id: int, form: dict, artifacts: dict,
                     jpeg_bytes: bytes, is_demo: bool = False) -> int:
        """
        @brief  发证备案:参数快照 + 成品存档(密文落盘 archive)
        @param  artifacts pipeline 返回(embed_w/h、wm_strength、engine_meta)
        """
        archive_path, archive_sha = self._store.seal_blob(
            jpeg_bytes, f"cv_archive:{tracer_id:012x}".encode())
        self._db.execute(
            "INSERT INTO cv_records(tracer_id, engine, cert_id, cert_label,"
            " cert_type, issuer_id, recipient, purpose, validity, visible_text,"
            " params_json, engine_meta_json, wm_bit_len, wm_strength, embed_w,"
            " embed_h, distort_seed, archive_path, archive_sha256,"
            " is_standalone, is_demo, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " 0, ?, ?)",
            (f"{tracer_id:012x}", engine,
             cert["id"] if cert else None,
             cert["label"] if cert else "", cert["cert_type"] if cert else "",
             issuer_id, form.get("recipient", ""), form.get("purpose", ""),
             form.get("validity", ""), form.get("visible_text", ""),
             json.dumps(form, ensure_ascii=False),
             json.dumps(artifacts["engine_meta"], ensure_ascii=False),
             artifacts["wm_bit_len"], artifacts["wm_strength"] or 0,
             artifacts["embed_w"], artifacts["embed_h"],
             int(form.get("distort_seed", 0)), archive_path, archive_sha,
             1 if is_demo else 0, _now()))
        return self._record_id(f"{tracer_id:012x}")

    def add_standalone(self, issuer_id: int, image_bytes: bytes, form: dict,
                       is_demo: bool = False) -> str:
        """
        @brief  独立备案(02-B1):登记外来图,不生成水印不可溯源,
                溯源候选自动剔除(is_standalone=1)
        """
        import secrets
        pseudo_tracer = f"sa{secrets.token_hex(5)}"
        archive_path, archive_sha = self._store.seal_blob(
            image_bytes, f"cv_archive:{pseudo_tracer}".encode())
        self._db.execute(
            "INSERT INTO cv_records(tracer_id, engine, issuer_id, recipient,"
            " purpose, validity, visible_text, params_json, archive_path,"
            " archive_sha256, is_standalone, is_demo, created_at)"
            " VALUES(?, 'standalone', ?, ?, ?, ?, '', ?, ?, ?, 1, ?, ?)",
            (pseudo_tracer, issuer_id, form.get("recipient", ""),
             form.get("purpose", ""), form.get("validity", ""),
             json.dumps(form, ensure_ascii=False), archive_path, archive_sha,
             1 if is_demo else 0, _now()))
        return pseudo_tracer

    def _record_id(self, tracer_hex: str) -> int:
        """@brief tracer → 主键"""
        rows = self._db.query(
            "SELECT id FROM cv_records WHERE tracer_id = ?", (tracer_hex,))
        return rows[0][0] if rows else None

    # ---- 查询 -----------------------------------------------------------
    def list_records(self, issuer_id: int, is_admin: bool) -> list:
        """@brief 台账列表(发证人可见自己的;管理员全量)"""
        if is_admin:
            rows = self._db.query(
                f"SELECT {RECORD_COLUMNS} FROM cv_records ORDER BY id DESC")
        else:
            rows = self._db.query(
                f"SELECT {RECORD_COLUMNS} FROM cv_records"
                " WHERE issuer_id = ? ORDER BY id DESC", (issuer_id,))
        return [_row_to_record(row) for row in rows]

    def get_by_tracer(self, tracer_hex: str) -> dict:
        """@brief 按溯源码取备案"""
        rows = self._db.query(
            f"SELECT {RECORD_COLUMNS} FROM cv_records WHERE tracer_id = ?",
            (tracer_hex,))
        return _row_to_record(rows[0]) if rows else None

    def traceable_candidates(self) -> list:
        """@brief 溯源候选=全部非独立备案(02-B1 独立备案自动剔除)"""
        rows = self._db.query(
            f"SELECT {RECORD_COLUMNS} FROM cv_records WHERE is_standalone = 0"
            " ORDER BY id DESC")
        return [_row_to_record(row) for row in rows]

    def read_archive(self, record: dict) -> bytes:
        """@brief 内存解密存档成品(下载接口用)"""
        return self._store.open_blob(
            record["archive_path"], f"cv_archive:{record['tracer_id']}".encode())

    # ---- 撤销(13-R-CV-5) ---------------------------------------------
    def revoke(self, tracer_hex: str, actor: str):
        """@brief 标记撤销(溯源仍命中但结果明示作废;留审计由路由层)"""
        record = self.get_by_tracer(tracer_hex)
        if record is None:
            raise PolicyValidationError("备案不存在")
        if record["revoked_at"]:
            raise PolicyValidationError("备案已处于撤销状态")
        self._db.execute(
            "UPDATE cv_records SET revoked_at = ?, revoked_by = ?"
            " WHERE tracer_id = ?", (_now(), actor, tracer_hex))

    def id_space_usage(self) -> dict:
        """@brief 48bit ID 空间用量监控(13-R-CV-5 管理页)"""
        rows = self._db.query(
            "SELECT COUNT(*) FROM cv_records WHERE is_standalone = 0")
        used = rows[0][0]
        return {"used": used, "capacity_bits": 48,
                "utilization": used / float(TRACER_MAX + 1)}

    # ---- 发证笔记(密文;越权 403 由路由层判 issuer/admin) ------------
    def save_note(self, record_id: int, location_ct: str, text_ct: str):
        """@brief 保存笔记密文(定位/备注)"""
        self._db.execute(
            "INSERT INTO cv_notes(record_id, location_ct, text_ct, created_at)"
            " VALUES(?, ?, ?, ?)", (record_id, location_ct, text_ct, _now()))

    def get_note(self, record_id: int) -> dict:
        """@brief 读笔记密文"""
        rows = self._db.query(
            "SELECT id, location_ct, text_ct FROM cv_notes WHERE record_id = ?",
            (record_id,))
        if not rows:
            return None
        return {"id": rows[0][0], "location_ct": rows[0][1],
                "text_ct": rows[0][2]}

    def add_note_image(self, note_id: int, blob_path: str, sha256: str):
        """@brief 记笔记图密文路径(≤3 张由路由层限)"""
        self._db.execute(
            "INSERT INTO cv_note_images(note_id, blob_path, blob_sha256,"
            " created_at) VALUES(?, ?, ?, ?)", (note_id, blob_path, sha256, _now()))

    # ---- 反馈回流(13-R-CV-2) -----------------------------------------
    def add_engine_feedback(self, tracer_hex: str, engine: str, medium: str,
                            hit: bool):
        """@brief 溯源结果回流(非 PI),供推荐器学习"""
        self._db.execute(
            "INSERT INTO cv_engine_feedback(tracer_id, engine, medium, hit,"
            " created_at) VALUES(?, ?, ?, ?, ?)",
            (tracer_hex, engine, medium, 1 if hit else 0, _now()))

    def feedback_stats(self) -> list:
        """@brief 按引擎×介质聚合命中率(推荐器输入)"""
        rows = self._db.query(
            "SELECT engine, medium, SUM(hit), COUNT(*) FROM cv_engine_feedback"
            " GROUP BY engine, medium")
        return [{"engine": row[0], "medium": row[1],
                 "hits": row[2], "total": row[3]} for row in rows]
