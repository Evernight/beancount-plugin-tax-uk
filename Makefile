.PHONY: lint test build clean all format deps-testing

deps-testing:
	@echo "Installing package with testing dependencies..."
	pip install -e ".[testing]"

all: format lint test build

format:
	@echo "Running ruff formatter..."
	ruff format src/ tests/

lint:
	@echo "Running ruff linter and formatter..."
	ruff check src/ tests/
	ruff format --check src/ tests/

test:
	@echo "Running tests..."
	pytest

test-capture:
	@echo "Running tests with output capture..."
	pytest --capture-output

build: clean
	@echo "Building package..."
	python -m build

init-dev:
	python -m venv .venv
	. .venv/bin/activate
	pip install -e ".[testing]"

clean:
	@echo "Cleaning build artifacts..."
	rm -rf build/ dist/ *.egg-info/ 

show-tests:
	@echo "Showing tests in Fava..."
	cd tests && ./show_tests_in_fava.sh