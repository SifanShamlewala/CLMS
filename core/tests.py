import datetime
from decimal import Decimal

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, QueryDict
from django.test import RequestFactory, SimpleTestCase, TestCase

from accounts.models import Department, StaffProfile
from academics.models import LeaveType
from core.permissions import (
    administrator_required, get_profile, is_administrator, require_administrator,
    require_role, role_required,
)
from core.templatetags.form_tags import get_item
from core.testutils import make_admin, make_department, make_staff
from core.validation import (
    DomainError, ensure_unique, optional_text, parse_choice, parse_date,
    parse_decimal, required_text, validate,
)

Role = StaffProfile.Role


def errors_of(call, *args, **kwargs):
    with_error = None
    try:
        call(*args, **kwargs)
    except DomainError as exc:
        with_error = exc.errors
    return with_error


class DomainErrorTests(SimpleTestCase):
    def test_string_becomes_form_error(self):
        self.assertEqual(DomainError("Boom").errors, {"form": "Boom"})

    def test_message_joins_all_messages(self):
        self.assertEqual(DomainError({"a": "One.", "b": "Two."}).message, "One. Two.")


class RequiredTextTests(SimpleTestCase):
    def test_valid_value_is_stripped(self):
        self.assertEqual(required_text("  Dean ", "title"), "Dean")

    def test_blank_none_and_whitespace_rejected(self):
        for value in ("", "   ", None):
            self.assertEqual(errors_of(required_text, value, "title", label="Title"),
                             {"title": "Title is required."})

    def test_too_long_rejected_but_exact_length_allowed(self):
        self.assertEqual(required_text("a" * 5, "t", max_length=5), "aaaaa")
        self.assertIn("at most 5", errors_of(required_text, "a" * 6, "t", max_length=5)["t"])

    def test_default_label_comes_from_field_name(self):
        self.assertEqual(errors_of(required_text, "", "leave_type"), {"leave_type": "Leave type is required."})


class OptionalTextTests(SimpleTestCase):
    def test_blank_returns_empty_string(self):
        self.assertEqual(optional_text("   ", "d"), "")
        self.assertEqual(optional_text(None, "d"), "")

    def test_value_is_stripped_and_length_checked(self):
        self.assertEqual(optional_text(" hi ", "d", 10), "hi")
        self.assertIn("at most 3", errors_of(optional_text, "abcd", "d", 3)["d"])


class ParseDateTests(SimpleTestCase):
    def test_valid_iso_date(self):
        self.assertEqual(parse_date(" 2026-11-10 ", "d"), datetime.date(2026, 11, 10))

    def test_bad_values_rejected(self):
        for value in ("", None, "10/11/2026", "2026-02-30", "tomorrow"):
            self.assertEqual(errors_of(parse_date, value, "start_date", label="Start date"),
                             {"start_date": "Start date must be a valid date."})


class ParseDecimalTests(SimpleTestCase):
    def test_valid_values(self):
        self.assertEqual(parse_decimal("1.5", "n"), Decimal("1.5"))
        self.assertEqual(parse_decimal(" 0 ", "n", min_value=0), Decimal("0"))

    def test_not_a_number_rejected(self):
        for value in ("", None, "abc", "NaN", "Infinity", "1,5"):
            self.assertIn("must be a number", errors_of(parse_decimal, value, "n")["n"])

    def test_min_and_max_enforced_inclusively(self):
        self.assertIn("at least 0", errors_of(parse_decimal, "-0.5", "n", min_value=0)["n"])
        self.assertIn("at most 10", errors_of(parse_decimal, "10.5", "n", max_value=10)["n"])
        self.assertEqual(parse_decimal("10", "n", max_value=10), Decimal("10"))


class ParseChoiceTests(SimpleTestCase):
    def test_valid_choice(self):
        self.assertEqual(parse_choice(" HOD ", "role", Role.values), "HOD")

    def test_invalid_or_blank_choice(self):
        for value in ("", None, "BOSS", "hod"):
            self.assertEqual(errors_of(parse_choice, value, "role", Role.values, label="Role"),
                             {"role": "Choose a valid role."})


class ValidateTests(SimpleTestCase):
    def test_returns_cleaned_values(self):
        cleaned = validate({
            "title": lambda: required_text(" A ", "title"),
            "when": lambda: parse_date("2026-01-02", "when"),
        })
        self.assertEqual(cleaned, {"title": "A", "when": datetime.date(2026, 1, 2)})

    def test_collects_every_error_at_once(self):
        errors = errors_of(validate, {
            "title": lambda: required_text("", "title", label="Title"),
            "when": lambda: parse_date("nope", "when", label="Date"),
            "ok": lambda: required_text("fine", "ok"),
        })
        self.assertEqual(errors, {"title": "Title is required.", "when": "Date must be a valid date."})


class EnsureUniqueTests(TestCase):
    def setUp(self):
        Department.objects.create(name="Physics")
        LeaveType.objects.create(name="Casual Leave", code="CL")

    def test_case_insensitive_clash_detected(self):
        errors = errors_of(ensure_unique, Department, {"name": "pHySiCs"}, {"name": "Taken."})
        self.assertEqual(errors, {"name": "Taken."})

    def test_no_clash_passes(self):
        ensure_unique(Department, {"name": "Chemistry"}, {"name": "Taken."})

    def test_exclude_pk_allows_own_record(self):
        own = Department.objects.get(name="Physics")
        ensure_unique(Department, {"name": "physics"}, {"name": "Taken."}, exclude_pk=own.pk)

    def test_reports_all_clashing_fields(self):
        errors = errors_of(
            ensure_unique, LeaveType, {"name": "casual leave", "code": "cl"},
            {"name": "Name taken.", "code": "Code taken."},
        )
        self.assertEqual(errors, {"name": "Name taken.", "code": "Code taken."})


class ProfileAndAdministratorTests(TestCase):
    def test_get_profile_for_anonymous_user_without_profile_and_with_profile(self):
        self.assertIsNone(get_profile(AnonymousUser()))
        self.assertIsNone(get_profile(make_admin()))
        staff = make_staff()
        self.assertEqual(get_profile(staff.user), staff)

    def test_is_administrator(self):
        self.assertTrue(is_administrator(make_admin()))
        self.assertFalse(is_administrator(make_staff().user))
        self.assertFalse(is_administrator(AnonymousUser()))

    def test_inactive_superuser_is_not_administrator(self):
        admin = make_admin()
        admin.is_active = False
        admin.save()
        self.assertFalse(is_administrator(admin))

    def test_require_administrator_raises_for_non_admin(self):
        with self.assertRaises(PermissionDenied):
            require_administrator(make_staff().user)
        require_administrator(make_admin("root"))


class DecoratorTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def call(self, view, user):
        request = self.factory.get("/x/")
        request.user = user
        return view(request)

    def test_administrator_required(self):
        @administrator_required
        def view(request):
            return HttpResponse("ok")

        self.assertEqual(self.call(view, AnonymousUser()).status_code, 302)
        with self.assertRaises(PermissionDenied):
            self.call(view, make_staff().user)
        self.assertEqual(self.call(view, make_admin()).status_code, 200)

    def test_role_required_allows_matching_active_role_only(self):
        @role_required(Role.HOD, Role.PRINCIPAL)
        def view(request):
            return HttpResponse("ok")

        dept = make_department()
        self.assertEqual(self.call(view, AnonymousUser()).status_code, 302)
        with self.assertRaises(PermissionDenied):
            self.call(view, make_staff("s", dept, Role.STAFF).user)
        with self.assertRaises(PermissionDenied):
            self.call(view, make_staff("h0", dept, Role.HOD, is_active=False).user)
        with self.assertRaises(PermissionDenied):
            self.call(view, make_admin())  # administrator without a profile has no role
        self.assertEqual(self.call(view, make_staff("h", dept, Role.HOD).user).status_code, 200)

    def test_require_role_denies_inactive_user(self):
        staff = make_staff("x", role=Role.HOD)
        staff.user.is_active = False
        staff.user.save()
        with self.assertRaises(PermissionDenied):
            require_role(staff.user, Role.HOD)


class GetItemFilterTests(SimpleTestCase):
    def test_dict_querydict_and_bad_input(self):
        self.assertEqual(get_item({"a": "1"}, "a"), "1")
        self.assertEqual(get_item({"a": "1"}, "b"), "")
        self.assertEqual(get_item(QueryDict("a=2"), "a"), "2")
        self.assertEqual(get_item(None, "a"), "")