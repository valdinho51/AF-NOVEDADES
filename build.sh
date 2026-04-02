#!/bin/bash
# Script de build para Render
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
