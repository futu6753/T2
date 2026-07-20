# 可复现说明

【内部资料】本文件由三包流水线自动生成,仅限申报准备使用。

- 全部基准离线一键运行,固定随机种子;数据表落 `benchmarks/data/`。
- 一键命令(在仓库根目录):
- `python3 benchmarks/adapter_onboard_benchmark.py`
- `python3 benchmarks/assist_security_benchmark.py`
- `python3 benchmarks/auth_availability_benchmark.py`
- `python3 benchmarks/debounce_replay.py`
- `python3 benchmarks/erasure_benchmark.py`
- `python3 benchmarks/f3d_scale_benchmark.py`
- `python3 benchmarks/mode_switch_benchmark.py`
- `python3 benchmarks/quiz_learning_benchmark.py`
- `python3 benchmarks/recapture_matrix.py`
- 环境:Python 3.12,依赖见 requirements.txt;无网络依赖。
