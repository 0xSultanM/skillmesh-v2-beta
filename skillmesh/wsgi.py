"""
WSGI config for SkillMesh AI project.
Uses WhiteNoise for static files and auto-migration on startup.
"""
import os
from django.core.wsgi import get_wsgi_application
from whitenoise import WhiteNoise

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'skillmesh.settings')

application = get_wsgi_application()
application = WhiteNoise(application, root=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'staticfiles'))
application.add_files(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'staticfiles'), prefix='static/')

# Auto-run migrations after Django is fully initialized
try:
    from django.core.management import call_command
    print("Running database migrations...")
    call_command('migrate', '--run-syncdb', verbosity=0)
    print("Migrations completed successfully.")
except Exception as e:
    print(f"Migration warning: {e}")
