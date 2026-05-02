#!/usr/bin/env bash
# Setup para Mac/Linux
set -e

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "Setup completo."
echo "Para correr la app:"
echo "  source .venv/bin/activate"
echo "  streamlit run app.py"
