# -*- coding: utf-8 -*-
"""
@file    engines.py
@brief   引擎注册表(02-B2 / 06-E7):四引擎 + 组合双保险。可用性探测进
         /engines 与 /health;不可用引擎在 /issue 被选中 → 400 人话原因
         (契约内合法状态,如「模型未安装」)。stega/tm 为模型驱动引擎:
         模型目录探测到权重才激活(TODO(GAP-13) 目标环境导入模型)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os

import numpy as np

from gd_common.errors import PolicyValidationError
from apps.certvault.wm import bw

ENGINE_BW = "bw"
ENGINE_STEGA = "stega"
ENGINE_TM = "tm"
ENGINE_ALIYUN = "aliyun"
COMBO_BW_STEGA = "bw+stega"
COMBO_BW_TM = "bw+tm"
ALL_ENGINE_IDS = (ENGINE_BW, ENGINE_STEGA, ENGINE_TM, ENGINE_ALIYUN,
                  COMBO_BW_STEGA, COMBO_BW_TM)
BLIND_TRY_ORDER = (ENGINE_TM, ENGINE_STEGA, ENGINE_ALIYUN)   # 溯源盲提顺序(契约)

STEGA_STRENGTH_DEFAULT = 1.2      # L02 config STEGA_STRENGTH
TM_STRENGTH_DEFAULT = 1.0


class BwEngine:
    """bw 频域盲水印(零依赖,恒可用)。"""

    engine_id = ENGINE_BW
    name = bw.ENGINE_NAME
    recommended_strength = bw.RECOMMENDED_STRENGTH

    def availability(self) -> tuple:
        """@brief (available, detail)"""
        return True, "本地频域引擎,毫秒级,电子链路可靠(不承诺翻拍)"

    def embed(self, y_channel: np.ndarray, tracer_id: int,
              strength: float) -> np.ndarray:
        """@brief 嵌入 96bit 载荷"""
        return bw.embed_tracer(y_channel, tracer_id, strength)

    def extract(self, y_channel: np.ndarray, strength: float) -> int:
        """@brief 盲提取 @return tracer_id 或 None"""
        return bw.extract_tracer(y_channel, strength)


class ModelDrivenEngineStub:
    """
    stega/tm 模型驱动引擎骨架:模型目录含权重才激活。
    TODO(GAP-13): 目标环境导入 StegaStamp ONNX(209MB)/TrustMark 权重后,
    在本类挂接推理实现并跑 L02 §8 双后端互操作与 tm 逐位对齐验收。
    """

    def __init__(self, engine_id: str, name: str, model_dir: str,
                 recommended_strength: float, unavailable_hint: str):
        """@brief 声明模型目录与人话提示"""
        self.engine_id = engine_id
        self.name = name
        self.recommended_strength = recommended_strength
        self._model_dir = model_dir
        self._hint = unavailable_hint

    def availability(self) -> tuple:
        """@brief 探测模型目录是否含权重文件(06-E7 探测进 /health)"""
        if self._model_dir and os.path.isdir(self._model_dir) \
                and any(entry.endswith((".onnx", ".bin", ".pt"))
                        for entry in os.listdir(self._model_dir)):
            return False, "模型文件已就位,推理挂接随 GAP-13 交付"
        return False, self._hint

    def embed(self, y_channel, tracer_id, strength):
        """@brief 不可用引擎防御性拒绝(调用方应先查可用性)"""
        raise PolicyValidationError(f"{self.name} 不可用:{self._hint}")

    def extract(self, y_channel, strength):
        """@brief 同上"""
        raise PolicyValidationError(f"{self.name} 不可用:{self._hint}")


class AliyunEngineStub:
    """aliyun 文字引擎:配置 AK 才启用(可选云引擎,02-B4)。"""

    engine_id = ENGINE_ALIYUN
    name = "阿里云文字水印"
    recommended_strength = 1.0

    def __init__(self, access_key_id: str = "", access_key_secret: str = ""):
        """@brief 注入 AK(空=未启用)"""
        self._enabled = bool(access_key_id and access_key_secret)

    def availability(self) -> tuple:
        """@brief AK 未配置即不可用;指南声明可疑图会上传云端"""
        if self._enabled:
            return False, "AK 已配置,SDK 挂接随 GAP-13 交付"
        return False, "未配置阿里云 AccessKey(可选引擎)"

    def embed(self, y_channel, tracer_id, strength):
        """@brief 未启用防御性拒绝"""
        raise PolicyValidationError("阿里云引擎未启用:未配置 AccessKey")

    def extract(self, y_channel, strength):
        """@brief 同上"""
        raise PolicyValidationError("阿里云引擎未启用:未配置 AccessKey")


class EngineRegistry:
    """引擎注册表:解析/可用性/组合双保险语义。"""

    def __init__(self, stega_model_dir: str = "", tm_model_dir: str = "",
                 aliyun_ak: str = "", aliyun_secret: str = "",
                 default_engine: str = ENGINE_BW):
        """@brief 装配四引擎(模型/AK 由部署配置注入)"""
        self.default_engine = default_engine
        self._engines = {
            ENGINE_BW: BwEngine(),
            ENGINE_STEGA: ModelDrivenEngineStub(
                ENGINE_STEGA, "深度学习隐写(StegaStamp)", stega_model_dir,
                STEGA_STRENGTH_DEFAULT,
                "模型未安装(需导入 StegaStamp ONNX 权重,约 209MB)"),
            ENGINE_TM: ModelDrivenEngineStub(
                ENGINE_TM, "TrustMark", tm_model_dir, TM_STRENGTH_DEFAULT,
                "模型未安装(需导入 TrustMark 权重)"),
            ENGINE_ALIYUN: AliyunEngineStub(aliyun_ak, aliyun_secret),
        }

    def get(self, engine_id: str):
        """@brief 取单引擎实例"""
        return self._engines[engine_id]

    def members(self, engine_id: str) -> list:
        """@brief 组合引擎成员展开(单引擎返回自身)"""
        return engine_id.split("+") if "+" in engine_id else [engine_id]

    def resolve(self, requested: str) -> str:
        """
        @brief  /issue 引擎解析与可用性校验(L02):空=系统默认;
                不可用 → PolicyValidationError 人话原因(HTTP 400)
        """
        engine_id = requested or self.default_engine
        if engine_id not in ALL_ENGINE_IDS:
            raise PolicyValidationError(f"未知引擎: {engine_id}")
        for member in self.members(engine_id):
            available, detail = self._engines[member].availability()
            if not available:
                raise PolicyValidationError(
                    f"引擎 {member} 不可用:{detail}")
        return engine_id

    def describe_all(self) -> list:
        """@brief GET /engines:全引擎可用性/详情/推荐强度/默认标记"""
        entries = []
        for engine_id in (ENGINE_BW, ENGINE_STEGA, ENGINE_TM, ENGINE_ALIYUN):
            engine = self._engines[engine_id]
            available, detail = engine.availability()
            entries.append({"id": engine_id, "name": engine.name,
                            "available": available, "detail": detail,
                            "recommended_strength": engine.recommended_strength,
                            "default": engine_id == self.default_engine})
        return entries

    def strength_for(self, engine_id: str, requested: float) -> float:
        """@brief wm_strength=-1 表示取引擎推荐值(L02)"""
        if requested is not None and requested >= 0:
            return requested
        return self._engines[engine_id].recommended_strength
