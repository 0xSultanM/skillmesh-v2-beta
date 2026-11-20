#!/bin/bash
# Vercel build script for SkillMesh AI

set -e

echo "=== Starting build process ==="

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Running collectstatic..."
python manage.py collectstatic --noinput --verbosity=2

echo "Verifying static files..."
if [ -d "staticfiles" ]; then
    echo "✓ staticfiles directory exists"
    echo "Contents:"
    find staticfiles -type f | head -20
else
    echo "✗ ERROR: staticfiles directory not created!"
    exit 1
fi

echo "=== Build complete ==="
