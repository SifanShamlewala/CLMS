from django.test import TestCase

from accounts.models import Department
from core.lookup import parse_object
from core.validation import DomainError


class ParseObjectTests(TestCase):
    def setUp(self):
        self.active = Department.objects.create(name="Physics")
        self.inactive = Department.objects.create(name="Old", is_active=False)

    def error_of(self, queryset, value):
        with self.assertRaises(DomainError) as ctx:
            parse_object(queryset, value, "department", "department")
        return ctx.exception.errors

    def test_valid_id_returns_the_object(self):
        found = parse_object(Department.objects.all(), f" {self.active.pk} ", "department", "department")
        self.assertEqual(found, self.active)

    def test_blank_none_and_text_rejected(self):
        for value in ("", "  ", None, "abc", "1.5"):
            self.assertEqual(self.error_of(Department.objects.all(), value),
                             {"department": "Choose a valid department."})

    def test_unknown_id_rejected(self):
        self.assertIn("department", self.error_of(Department.objects.all(), "9999"))

    def test_queryset_limits_the_allowed_choices(self):
        only_active = Department.objects.filter(is_active=True)
        self.assertIn("department", self.error_of(only_active, str(self.inactive.pk)))
