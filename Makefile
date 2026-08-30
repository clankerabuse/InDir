.PHONY: install uninstall doctor test

install:
	@chmod +x install scripts/install.sh scripts/uninstall.sh scripts/*.py
	@./install

uninstall:
	@chmod +x scripts/uninstall.sh
	@./scripts/uninstall.sh

doctor:
	@.venv/bin/python scripts/doctor.py

test:
	@.venv/bin/pytest -v
