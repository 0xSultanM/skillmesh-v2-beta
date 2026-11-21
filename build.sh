#!/bin/bash
set -e

echo "=== SkillMesh AI Build ==="
echo "Installing dependencies..."
pip install -r requirements.txt

echo "Creating staticfiles directory..."
mkdir -p staticfiles

echo "Running collectstatic..."
python manage.py collectstatic --noinput --clear 2>&1

echo "Verifying static files..."
if [ -d "staticfiles/web" ]; then
    echo "✓ staticfiles/web directory exists"
    ls -la staticfiles/web/
else
    echo "✗ ERROR: staticfiles/web directory not created!"
    exit 1
fi

echo "=== Build complete ==="
