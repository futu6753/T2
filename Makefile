# 港电实验室统一平台 Makefile(H10 三包流水线 + 常用工序)
.PHONY: test ci copyright-pack paper-pack patent-pack packs benchmarks

test:
	python3 -m unittest discover -s tests -q

ci:
	bash ci_gate.sh

copyright-pack:
	python3 scripts/make_packs.py copyright

paper-pack:
	python3 scripts/make_packs.py paper

patent-pack:
	python3 scripts/make_packs.py patent

packs:
	python3 scripts/make_packs.py all

benchmarks:
	python3 benchmarks/auth_availability_benchmark.py
	python3 benchmarks/mode_switch_benchmark.py
	python3 benchmarks/assist_security_benchmark.py
	python3 benchmarks/f3d_scale_benchmark.py
	python3 benchmarks/debounce_replay.py
	python3 benchmarks/adapter_onboard_benchmark.py
	python3 benchmarks/quiz_learning_benchmark.py
