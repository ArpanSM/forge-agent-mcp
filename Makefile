PYTHON ?= python3
PIP_FLAGS ?=

.PHONY: install corpus test lint

install:
	$(PYTHON) -m pip install $(PIP_FLAGS) -e ".[dev]"

corpus:
	$(PYTHON) -m forge_agent_mcp.corpus generate --out data/corpus

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
