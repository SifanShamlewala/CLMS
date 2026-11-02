from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase

BANNED = (
    "django.contrib.admin",
    "from django.contrib import admin",
    "django.forms",
    "from django import forms",
)


class ProjectRulesTest(SimpleTestCase):
    def test_admin_not_installed(self):
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)

    def test_no_admin_or_django_forms_in_code(self):
        offenders = []
        for path in Path(settings.BASE_DIR).rglob("*.py"):
            if ".venv" in path.parts or path.name == "test_rules.py":
                continue
            if any(b in path.read_text(encoding="utf-8") for b in BANNED):
                offenders.append(str(path))
        self.assertEqual(offenders, [])