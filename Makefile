.PHONY: install test lint build

install:
	pnpm install --frozen-lockfile

test:
	pnpm test

lint:
	pnpm check

build:
	pnpm build
