#!/bin/bash
set -e
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
python -m playwright install-deps chromium
