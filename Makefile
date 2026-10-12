# Convenience targets. PYTHONPATH=src makes the CLI independent of the editable-install .pth, which macOS can
# flag as hidden (Python 3.13+ then silently skips it).
PY ?= .venv/bin/python
BR = PYTHONPATH=src $(PY) -m blastradius
TARGET ?= hoppscotch
PR ?= 6645

.PHONY: install doctor test test-fast lint up down crawl ingest index load link gaps flows pr eval demo

install:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -e ".[dev]"
	.venv/bin/playwright install chromium

up:
	docker compose up -d --wait

down:
	docker compose down

doctor:
	$(BR) doctor -t $(TARGET)

test:
	$(PY) -m pytest

test-fast:
	$(PY) -m pytest -m "not slow"

lint:
	.venv/bin/ruff check src tests

crawl:
	$(BR) crawl -t $(TARGET) --new
ingest:
	$(BR) ingest -t $(TARGET)
index:
	$(BR) index -t $(TARGET)
load:
	$(BR) load -t $(TARGET)
link:
	$(BR) link -t $(TARGET)
gaps:
	$(BR) gaps -t $(TARGET)
flows:
	$(BR) flows -t $(TARGET)
pr:
	$(BR) pr $(PR) -t $(TARGET)
eval:
	$(BR) eval -t $(TARGET)

# The full pipeline on one target, in the order the stages depend on each other.
demo: doctor crawl ingest index load link gaps flows pr eval
