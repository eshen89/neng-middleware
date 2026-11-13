#!/usr/bin/env bash
set -euo pipefail

rm -rf .layer
mkdir -p .layer/nodejs
cp package.json pnpm-lock.yaml .layer/nodejs/
pnpm --dir .layer/nodejs install --ignore-workspace --prod --frozen-lockfile --node-linker=hoisted --ignore-scripts "$@"

modules=.layer/nodejs/node_modules
mkdir "$modules/neng-middleware"
cp -R dist package.json "$modules/neng-middleware/"
rm -f .layer/nodejs/{package.json,pnpm-lock.yaml} "$modules/.modules.yaml"
