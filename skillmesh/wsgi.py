"""
WSGI config for SkillMesh AI project.
Includes auto-migration on startup for Vercel deployment.
"""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'skillmesh.settings')

# Initialize Django first
application = get_wsgi_application()

# Auto-run migrations AFTER Django is fully initialized
try:
    from django.core.management import call_command
    print("Running database migrations...")
    call_command('migrate', '--run-syncdb', verbosity=0)
    print("Migrations completed successfully.")
except Exception as e:
    print(f"Migration warning: {e}")
