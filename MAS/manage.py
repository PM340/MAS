#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import glob
import os
import site
import sys


def _activate_local_venv():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for site_dir in glob.glob(os.path.join(base_dir, '.venv', 'lib', 'python*', 'site-packages')):
        if os.path.isdir(site_dir) and site_dir not in sys.path:
            site.addsitedir(site_dir)


def main():
    """Run administrative tasks."""
    _activate_local_venv()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lms_project.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
