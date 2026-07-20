# -*- coding: utf-8 -*-
"""
@file    visible.py
@brief   可见层处理(02-B3):①微扭曲(默认 1.2px 随机相位正弦场)、
         ②抗擦除明水印(30° 平铺文字 + 团花网纹)、智能锚定(文字行形态学
         梯度简化目标)。中文文字用 PIL 默认字体渲染后仿射平铺。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

DEFAULT_DISTORT_AMPLITUDE = 1.2       # 微扭曲默认振幅 px(L02)
DEFAULT_OPACITY = 0.18                # 明水印默认浓度(L02)
DEFAULT_COLOR = (90, 120, 130)        # 默认 RGB(L02 color_r,g,b)
TILE_ANGLE_DEG = 30                   # 平铺角度(02-B3)
GUILLOCHE_WAVES = 7                   # 团花网纹叶数


def micro_distort(image: np.ndarray, amplitude: float,
                  seed: int) -> np.ndarray:
    """
    @brief  微扭曲:双向低频正弦位移场(随机相位由 seed 复现,
            备案记录 distort_seed 供回配)
    """
    if amplitude <= 0:
        return image
    height, width = image.shape[:2]
    rng = np.random.default_rng(seed)
    phase_x, phase_y = rng.uniform(0, 2 * np.pi, 2)
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32),
                                 np.arange(height, dtype=np.float32))
    map_x = grid_x + amplitude * np.sin(grid_y / 24.0 + phase_x).astype(np.float32)
    map_y = grid_y + amplitude * np.sin(grid_x / 28.0 + phase_y).astype(np.float32)
    return cv2.remap(image, map_x, map_y, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)


def _text_tile(text: str, color: tuple) -> np.ndarray:
    """@brief 渲染单元文字块(RGBA)供 30° 平铺"""
    font = ImageFont.load_default(size=22)
    probe = Image.new("RGBA", (8, 8))
    box = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
    tile = Image.new("RGBA", (box[2] + 48, box[3] + 32), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((24, 16), text, font=font, fill=(*color, 255))
    return np.array(tile)


def visible_watermark(image: np.ndarray, text: str,
                      opacity: float = DEFAULT_OPACITY,
                      color: tuple = DEFAULT_COLOR,
                      density: float = 1.0,
                      guilloche: bool = True) -> np.ndarray:
    """
    @brief  抗擦除明水印:30° 平铺文字 + 团花网纹叠加(02-B3 ②)
    @param  density 平铺密度倍率(1.0=默认间距)
    """
    height, width = image.shape[:2]
    overlay = np.zeros((height, width, 4), dtype=np.uint8)
    tile = _text_tile(text, color)
    step_y = max(int(tile.shape[0] * 2.4 / max(density, 0.1)), tile.shape[0] + 8)
    step_x = max(int(tile.shape[1] * 1.6 / max(density, 0.1)), tile.shape[1] + 8)
    canvas = Image.fromarray(overlay)
    tile_img = Image.fromarray(tile).rotate(TILE_ANGLE_DEG, expand=True)
    for offset_y in range(-tile_img.height, height + tile_img.height, step_y):
        row_shift = (offset_y // step_y % 2) * (step_x // 2)
        for offset_x in range(-tile_img.width - row_shift,
                              width + tile_img.width, step_x):
            canvas.alpha_composite(tile_img, (offset_x + row_shift, offset_y))
    overlay = np.array(canvas)
    if guilloche:
        overlay = _add_guilloche(overlay, color)
    alpha = (overlay[:, :, 3:4].astype(np.float32) / 255.0) * opacity
    blended = image.astype(np.float32) * (1 - alpha) \
        + overlay[:, :, :3].astype(np.float32) * alpha
    return np.clip(blended, 0, 255).astype(np.uint8)


def _add_guilloche(overlay: np.ndarray, color: tuple) -> np.ndarray:
    """@brief 团花网纹:玫瑰线参数曲线细描(增大擦除代价)"""
    height, width = overlay.shape[:2]
    center = (width // 2, height // 2)
    radius = min(width, height) * 0.42
    theta = np.linspace(0, 2 * np.pi * GUILLOCHE_WAVES, 2400)
    rho = radius * (0.55 + 0.45 * np.cos(GUILLOCHE_WAVES * theta / 2))
    points = np.stack([center[0] + rho * np.cos(theta),
                       center[1] + rho * np.sin(theta)], axis=1).astype(np.int32)
    cv2.polylines(overlay, [points.reshape(-1, 1, 2)], False,
                  (*color, 140), 1, cv2.LINE_AA)
    return overlay


def smart_anchor_lines(image: np.ndarray, color: tuple,
                       opacity: float) -> np.ndarray:
    """
    @brief  智能锚定(简化目标一:文字行形态学梯度定位)——在文字密集行
            下方压细锚定线,提高擦除后可察觉性(02-B2 智能锚定)
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gradient = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)))
    row_energy = gradient.astype(np.float32).mean(axis=1)
    threshold = row_energy.mean() + row_energy.std()
    output = image.copy()
    active = np.where(row_energy > threshold)[0]
    for row in active[::12]:                       # 稀疏取行防糊面
        y = min(row + 4, output.shape[0] - 1)
        line = output[y:y + 1, :].astype(np.float32)
        tint = np.array(color, dtype=np.float32)
        output[y:y + 1, :] = np.clip(
            line * (1 - opacity) + tint * opacity, 0, 255).astype(np.uint8)
    return output
