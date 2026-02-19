#!/usr/bin/env bash
# Render build script: install Python deps + build Vue frontend
set -o errexit

pip install -r requirements.txt

cd frontend
npm ci
npm run vercel-build
