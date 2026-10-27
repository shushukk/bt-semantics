PY ?= python3

.PHONY: help setup test demo bench rules clean

help:
	@echo "make setup  - 安装依赖（z3-solver, pytest）"
	@echo "make test   - 跑单元测试"
	@echo "make demo   - 跑两个示例的验证"
	@echo "make bench  - 规模基准（输出 build/bench.*）"
	@echo "make rules  - 导出 LATEX 格式的 SOS 规则"

setup:
	$(PY) -m pip install -r requirements.txt

test:
	$(PY) -m pytest -q

demo:
	$(PY) -m btfsv verify examples/guard.json
	$(PY) -m btfsv verify examples/buggy.json

bench:
	$(PY) scripts/bench_scale.py

rules:
	mkdir -p build
	$(PY) -m btfsv rules --format latex --out build/rules.tex
	$(PY) -m btfsv rules --format markdown --out build/rules.md

clean:
	rm -rf build .pytest_cache __pycache__ btfsv/__pycache__ tests/__pycache__
