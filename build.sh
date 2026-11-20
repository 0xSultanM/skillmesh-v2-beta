#!/bin/bash
set -e

echo "=== SkillMesh AI Build ==="
echo "Installing dependencies..."
pip install -r requirements.txt

echo "Collecting static files..."
python manage.py collectstatic --noinput --clear

echo "Verifying static files..."
ls -la staticfiles/web/ 2>/dev/null || echo "WARNING: No static files found"

echo "=== Build complete ==="
