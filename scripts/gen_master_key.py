#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    gen_master_key.py
@brief   生成 64 位 hex 主密钥(H04 §五):仅打印到终端,由运维注入环境变量;
         任何真实密钥不得写入代码库、交付包、文档(H00 G7)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import secrets

MASTER_KEY_BYTES = 32


def main():
    """@brief 生成并打印主密钥与保管指引"""
    key_hex = secrets.token_hex(MASTER_KEY_BYTES)
    print("已生成主密钥(仅显示一次,请立即抄录到密钥保管处):")
    print(f"  MASTER_KEY_HEX={key_hex}")
    print("保管要求:.env 文件权限 600、离线备份、禁截图外传(H04 §九);")
    print("更换主密钥必须走 rotate_master_key 迁移脚本,直接改配置会导致存量不可解(H06-E10)。")


if __name__ == "__main__":
    main()
