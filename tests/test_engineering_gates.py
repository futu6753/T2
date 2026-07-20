# -*- coding: utf-8 -*-
"""
@file    test_engineering_gates.py
@brief   工程质量门禁回归(H09 §二 E):P0-1 金丝雀假密钥必命中、本仓库零命中、
         文件 ≤500 行 / 禁 except:pass / 禁裸 except / 禁 print 进共享库
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import ast
import os
import tempfile
import unittest

from tests.base import REPO_ROOT

from scripts.scan_secrets import scan_tree

MAX_FILE_LINES = 500        # H01 §三:单文件 ≤500 行
PACKAGE_DIRS = ("packages",)


def _iter_repo_python_files():
    """@brief 遍历仓库共享库 Python 文件"""
    for base in PACKAGE_DIRS:
        for dirpath, _, filenames in os.walk(os.path.join(REPO_ROOT, base)):
            for name in filenames:
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


class TestSecretScan(unittest.TestCase):
    """敏感信息扫描(H06-P0-1/P0-2)。"""

    def test_p0_1_canary_secret_detected(self):
        """CI 金丝雀:构造假密钥文件,扫描必命中(P0-1 回归要求)"""
        with tempfile.TemporaryDirectory() as tmp:
            canary_path = os.path.join(tmp, "leaked.md")
            fake_hex = "0123456789abcdef" * 4
            pem_header = "-----BEGIN " + "RSA PRIVATE KEY-----"
            with open(canary_path, "w", encoding="utf-8") as handle:
                handle.write(f"master_key_hex = \"{fake_hex}\"\n")
                handle.write(pem_header + "\n")
                handle.write("AKIA" + "ABCDEFGHIJKL" + "MNOP\n")
            findings = scan_tree(tmp)
            rule_names = {finding[2] for finding in findings}
            self.assertGreaterEqual(len(findings), 3)
            self.assertIn("PEM 私钥", rule_names)
            self.assertIn("64 位 hex 密钥赋值", rule_names)

    def test_p0_1_repo_zero_hits(self):
        """本仓库(含 .md 文件)敏感信息扫描零命中(H09 E 组门禁)"""
        self.assertEqual(scan_tree(REPO_ROOT), [])


class TestCodingStandards(unittest.TestCase):
    """编码规范静态门禁(H07 映射)。"""

    def test_h07_file_length_limit(self):
        """共享库单文件 ≤500 行(H01 §三)"""
        for path in _iter_repo_python_files():
            with open(path, "r", encoding="utf-8") as handle:
                line_count = sum(1 for _ in handle)
            self.assertLessEqual(line_count, MAX_FILE_LINES,
                                 f"{os.path.relpath(path, REPO_ROOT)} 超过 500 行")

    def test_l104_no_silent_except(self):
        """禁 except: pass 与裸 except(H07 L1-04)"""
        for path in _iter_repo_python_files():
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    rel = os.path.relpath(path, REPO_ROOT)
                    is_silent_pass = (len(node.body) == 1
                                      and isinstance(node.body[0], ast.Pass))
                    self.assertFalse(is_silent_pass,
                                     f"{rel}:{node.lineno} 存在 except: pass")
                    # 裸 except 唯一豁免:迁移探测(migrations.applied_versions)
                    if node.type is None and "migrations.py" not in rel:
                        self.fail(f"{rel}:{node.lineno} 存在裸 except")

    def test_l3_no_print_in_packages(self):
        """共享库禁 print,统一结构化日志(H07 L3 日志条款)"""
        for path in _iter_repo_python_files():
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                is_print = (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "print")
                self.assertFalse(is_print,
                                 f"{os.path.relpath(path, REPO_ROOT)}:{getattr(node, 'lineno', 0)}"
                                 " 共享库出现 print")


if __name__ == "__main__":
    unittest.main()
