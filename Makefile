PYTHON ?= python3
PIP_FLAGS ?=
PYTHONPATH ?= src
CASE ?=
OFFLINE ?= 0

export PYTHONPATH

.PHONY: install corpus test lint extract

install:
	$(PYTHON) -m pip install $(PIP_FLAGS) -e ".[dev]"

corpus:
	$(PYTHON) -m forge_agent_mcp.corpus generate --out data/corpus

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

extract:
	@test -n "$(CASE)" || (echo "usage: make extract CASE=<case_id>" >&2; exit 2)
	@test -f data/corpus/corpus.json || $(MAKE) corpus
	$(PYTHON) -m forge_agent_mcp.extraction --case "$(CASE)" $(if $(filter 1,$(OFFLINE)),--offline,)
