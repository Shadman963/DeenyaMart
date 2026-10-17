#!/usr/bin/env bash
# Render build script for DeenyaMart.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py bootstrap_deploy
