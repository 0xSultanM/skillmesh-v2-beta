"""
WSGI config for SkillMesh AI project.
Static files are served by WhiteNoiseMiddleware (see settings.py).
"""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'skillmesh.settings')

application = get_wsgi_application()


def _run_setup():
    """Migrate + seed. Only runs when RUN_STARTUP_SETUP=1."""
    from django.core.management import call_command
    from django.db import connection

    print("Running database migrations...")
    call_command('migrate', verbosity=0)

    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM taxonomy_skill")
        skill_count = cursor.fetchone()[0]

    if skill_count == 0:
        print("Seeding initial data...")
        call_command('seed_taxonomy', verbosity=0)
        call_command('seed_resources', verbosity=0)
        call_command('seed_videos', verbosity=0)

        admin_user = os.getenv('DJANGO_ADMIN_USERNAME')
        admin_email = os.getenv('DJANGO_ADMIN_EMAIL')
        admin_pass = os.getenv('DJANGO_ADMIN_PASSWORD')
        if admin_user and admin_email and admin_pass:
            from django.contrib.auth.models import User
            if not User.objects.filter(username=admin_user).exists():
                User.objects.create_superuser(admin_user, admin_email, admin_pass)
    print("✓ Database setup complete.")


if os.getenv('RUN_STARTUP_SETUP') == '1':
    try:
        _run_setup()
    except Exception as e:
        print(f"Setup warning: {e}")