"""Business logic for designations, departments and staff.

Views never touch the models directly for writes: they call these functions,
which check permission, validate, and write inside a transaction.
"""
import logging
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from core.lookup import parse_object
from core.permissions import require_administrator
from core.validation import (
    CONFLICT_MESSAGE, DomainError, ensure_unique, optional_text, parse_choice,
    parse_date, required_text, validate,
)

from .models import Department, Designation, StaffProfile

User = get_user_model()
Role = StaffProfile.Role

logger = logging.getLogger("vsclms.setup")

DUPLICATE_TITLE = "A designation with this title already exists."
DUPLICATE_DEPARTMENT = "A department with this name already exists."


# ---------------------------------------------------------------- designations
def _clean_designation(data):
    return validate({
        "title": lambda: required_text(data.get("title"), "title", 100, "Title"),
    })


def create_designation(*, data, actor):
    require_administrator(actor)
    cleaned = _clean_designation(data)
    try:
        with transaction.atomic():
            ensure_unique(Designation, {"title": cleaned["title"]}, {"title": DUPLICATE_TITLE})
            designation = Designation.objects.create(title=cleaned["title"])
    except IntegrityError:  # a concurrent request created the same title first
        raise DomainError({"title": DUPLICATE_TITLE}) from None
    logger.info("designation created id=%s title=%r by=%s", designation.pk, designation.title, actor.pk)
    return designation


def update_designation(*, designation, data, actor):
    require_administrator(actor)
    cleaned = _clean_designation(data)
    try:
        with transaction.atomic():
            ensure_unique(
                Designation, {"title": cleaned["title"]}, {"title": DUPLICATE_TITLE},
                exclude_pk=designation.pk,
            )
            designation.title = cleaned["title"]
            designation.save(update_fields=["title"])
    except IntegrityError:
        raise DomainError({"title": DUPLICATE_TITLE}) from None
    logger.info("designation updated id=%s title=%r by=%s", designation.pk, designation.title, actor.pk)
    return designation


def set_designation_active(*, designation, active, actor):
    require_administrator(actor)
    designation.is_active = bool(active)
    designation.save(update_fields=["is_active"])
    logger.info("designation active=%s id=%s by=%s", designation.is_active, designation.pk, actor.pk)
    return designation


# ----------------------------------------------------------------- departments
def _clean_department(data):
    return validate({
        "name": lambda: required_text(data.get("name"), "name", 100, "Name"),
    })


def create_department(*, data, actor):
    require_administrator(actor)
    cleaned = _clean_department(data)
    try:
        with transaction.atomic():
            ensure_unique(Department, {"name": cleaned["name"]}, {"name": DUPLICATE_DEPARTMENT})
            department = Department.objects.create(name=cleaned["name"])
    except IntegrityError:
        raise DomainError({"name": DUPLICATE_DEPARTMENT}) from None
    logger.info("department created id=%s name=%r by=%s", department.pk, department.name, actor.pk)
    return department


def update_department(*, department, data, actor):
    require_administrator(actor)
    cleaned = _clean_department(data)
    try:
        with transaction.atomic():
            ensure_unique(
                Department, {"name": cleaned["name"]}, {"name": DUPLICATE_DEPARTMENT},
                exclude_pk=department.pk,
            )
            department.name = cleaned["name"]
            department.save(update_fields=["name"])
    except IntegrityError:
        raise DomainError({"name": DUPLICATE_DEPARTMENT}) from None
    logger.info("department updated id=%s name=%r by=%s", department.pk, department.name, actor.pk)
    return department


def set_department_active(*, department, active, actor):
    """Deactivate or reactivate a department.

    A department that still has active staff cannot be deactivated, because
    those staff would be left without a working department.
    """
    require_administrator(actor)
    active = bool(active)
    if not active:
        with transaction.atomic():
            remaining = department.staff.filter(is_active=True).count()
            if remaining:
                raise DomainError({
                    "form": (
                        f"{department.name} still has {remaining} active staff "
                        f"member{'s' if remaining != 1 else ''}. Deactivate or move "
                        "them before deactivating the department."
                    )
                })
            department.is_active = False
            department.save(update_fields=["is_active"])
    else:
        department.is_active = True
        department.save(update_fields=["is_active"])
    logger.info("department active=%s id=%s by=%s", department.is_active, department.pk, actor.pk)
    return department


# ----------------------------------------------------------------------- staff
USERNAME_TAKEN = "This username is already taken."
EMPLOYEE_TAKEN = "A staff member with this employee ID already exists."
USERNAME_PATTERN = re.compile(r"[\w.@+-]+")
PHONE_PATTERN = re.compile(r"[0-9+()\- ]+")


def _username(value):
    username = required_text(value, "username", 150, "Username")
    if not USERNAME_PATTERN.fullmatch(username):
        raise DomainError({"username": "Username may contain only letters, numbers and . @ + - _"})
    return username


def _email(value):
    email = optional_text(value, "email", 254, "Email")
    if email:
        try:
            validate_email(email)
        except DjangoValidationError:
            raise DomainError({"email": "Enter a valid email address."}) from None
    return email


def _phone(value):
    phone = optional_text(value, "phone", 15, "Phone")
    if phone and not PHONE_PATTERN.fullmatch(phone):
        raise DomainError({"phone": "Phone may contain only digits, spaces and + - ( )."})
    return phone


def _optional_date(value, field, label):
    if not (value or "").strip():
        return None
    return parse_date(value, field, label)


def _department(value, current):
    department = parse_object(Department.objects.all(), value, "department", "department")
    if not department.is_active and not (current and current.department_id == department.pk):
        raise DomainError({"department": f"{department.name} is inactive. Choose another department."})
    return department


def _designation(value, current):
    if not (value or "").strip():
        return None
    designation = parse_object(Designation.objects.all(), value, "designation", "designation")
    if not designation.is_active and not (current and current.designation_id == designation.pk):
        raise DomainError({"designation": f"{designation.title} is inactive. Choose another designation."})
    return designation


def _clean_staff(data, current=None):
    """Validate the staff form. ``current`` is the profile being edited, if any."""
    checks = {
        "first_name": lambda: required_text(data.get("first_name"), "first_name", 150, "First name"),
        "last_name": lambda: optional_text(data.get("last_name"), "last_name", 150, "Last name"),
        "email": lambda: _email(data.get("email")),
        "employee_id": lambda: required_text(data.get("employee_id"), "employee_id", 30, "Employee ID"),
        "department": lambda: _department(data.get("department"), current),
        "designation": lambda: _designation(data.get("designation"), current),
        "role": lambda: parse_choice(data.get("role"), "role", Role.values, "Role"),
        "phone": lambda: _phone(data.get("phone")),
        "joined_on": lambda: _optional_date(data.get("joined_on"), "joined_on", "Joining date"),
    }
    if current is None:  # the username is fixed once the account exists
        checks["username"] = lambda: _username(data.get("username"))
    return validate(checks)


def _clean_password(data, user=None):
    password = data.get("password") or ""
    confirm = data.get("password2") or ""
    if not password:
        raise DomainError({"password": "Password is required."})
    if password != confirm:
        raise DomainError({"password2": "The two passwords don't match."})
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise DomainError({"password": " ".join(exc.messages)}) from None
    return password


def _clean_new_staff(data):
    """Validate the fields and the password together so every problem shows at once."""
    errors, cleaned = {}, None
    try:
        cleaned = _clean_staff(data)
    except DomainError as exc:
        errors.update(exc.errors)
    stub = None
    if cleaned:
        stub = User(
            username=cleaned["username"], first_name=cleaned["first_name"],
            last_name=cleaned["last_name"], email=cleaned["email"],
        )
    password = None
    try:
        password = _clean_password(data, stub)
    except DomainError as exc:
        errors.update(exc.errors)
    if errors:
        raise DomainError(errors)
    return cleaned, password


def _display_name(profile):
    return profile.user.get_full_name() or profile.user.get_username()


def _collect_unique(errors, model, values, messages, exclude_pk=None):
    try:
        ensure_unique(model, values, messages, exclude_pk=exclude_pk)
    except DomainError as exc:
        errors.update(exc.errors)


def _ensure_staff_unique(cleaned, profile=None):
    errors = {}
    if profile is None:
        _collect_unique(errors, User, {"username": cleaned["username"]}, {"username": USERNAME_TAKEN})
    _collect_unique(
        errors, StaffProfile, {"employee_id": cleaned["employee_id"]},
        {"employee_id": EMPLOYEE_TAKEN}, exclude_pk=profile.pk if profile else None,
    )
    if errors:
        raise DomainError(errors)


def _check_role_rules(role, department, profile=None, check_head=True):
    """Rules the database cannot express on its own.

    * Only one Principal may exist (the database also enforces this).
    * A department has at most one head, and a head belongs to that department.
    """
    own_pk = profile.pk if profile is not None else None
    if role == Role.PRINCIPAL:
        others = StaffProfile.objects.select_related("user").filter(role=Role.PRINCIPAL)
        if own_pk is not None:
            others = others.exclude(pk=own_pk)
        other = others.first()
        if other is not None:
            raise DomainError({
                "role": f"{_display_name(other)} is already the Principal. Change that profile's role first."
            })
    if role == Role.HOD and check_head:
        head = department.hod
        if head is not None and head.pk != own_pk:
            raise DomainError({
                "department": (
                    f"{_display_name(head)} already heads {department.name}. "
                    "Change their role first, or choose another department."
                )
            })


def _sync_department_head(profile):
    """Keep ``Department.hod`` consistent with this profile.

    An active HOD heads exactly their own department. Anyone else (demoted,
    moved or deactivated) is removed as head. Returns notes for the administrator.
    """
    notes = []
    should_head = profile.role == Role.HOD and profile.is_active
    for dept in list(Department.objects.filter(hod=profile)):
        if not should_head or dept.pk != profile.department_id:
            dept.hod = None
            dept.save(update_fields=["hod"])
            notes.append(
                f"{dept.name} has no head of department now. Assign one before its staff apply for leave."
            )
    if should_head:
        dept = profile.department
        if dept.hod_id != profile.pk:
            dept.hod = profile
            dept.save(update_fields=["hod"])
    return notes


def create_staff(*, data, actor):
    """Create the login and the staff profile together, or neither."""
    require_administrator(actor)
    cleaned, password = _clean_new_staff(data)
    try:
        with transaction.atomic():
            _ensure_staff_unique(cleaned)
            department = Department.objects.select_for_update().get(pk=cleaned["department"].pk)
            _check_role_rules(cleaned["role"], department)
            user = User.objects.create_user(
                username=cleaned["username"], email=cleaned["email"], password=password,
                first_name=cleaned["first_name"], last_name=cleaned["last_name"],
            )
            profile = StaffProfile.objects.create(
                user=user, employee_id=cleaned["employee_id"], department=department,
                designation=cleaned["designation"], role=cleaned["role"],
                phone=cleaned["phone"], joined_on=cleaned["joined_on"],
            )
            _sync_department_head(profile)
    except IntegrityError:  # a concurrent request took the username, employee ID or Principal seat
        raise DomainError({"form": CONFLICT_MESSAGE}) from None
    logger.info(
        "audit action=STAFF_CREATED id=%s employee_id=%s role=%s by=%s",
        profile.pk, profile.employee_id, profile.role, actor.pk,
    )
    return profile


def update_staff(*, profile, data, actor):
    """Edit a staff member. Returns ``(profile, notes)``."""
    require_administrator(actor)
    cleaned = _clean_staff(data, current=profile)
    try:
        with transaction.atomic():
            profile = StaffProfile.objects.select_related("user").select_for_update().get(pk=profile.pk)
            _ensure_staff_unique(cleaned, profile)
            department = Department.objects.select_for_update().get(pk=cleaned["department"].pk)
            _check_role_rules(cleaned["role"], department, profile, check_head=profile.is_active)
            previous_role = profile.role

            user = profile.user
            user.first_name = cleaned["first_name"]
            user.last_name = cleaned["last_name"]
            user.email = cleaned["email"]
            user.save(update_fields=["first_name", "last_name", "email"])

            profile.employee_id = cleaned["employee_id"]
            profile.department = department
            profile.designation = cleaned["designation"]
            profile.role = cleaned["role"]
            profile.phone = cleaned["phone"]
            profile.joined_on = cleaned["joined_on"]
            profile.save(update_fields=[
                "employee_id", "department", "designation", "role", "phone", "joined_on",
            ])
            notes = _sync_department_head(profile)
    except IntegrityError:
        raise DomainError({"form": CONFLICT_MESSAGE}) from None
    if previous_role != profile.role:
        logger.info(
            "audit action=STAFF_ROLE_CHANGED id=%s from=%s to=%s by=%s",
            profile.pk, previous_role, profile.role, actor.pk,
        )
    logger.info("audit action=STAFF_UPDATED id=%s by=%s", profile.pk, actor.pk)
    return profile, notes


def set_staff_active(*, profile, active, actor):
    """Activate or deactivate the profile and the login together. Returns ``(profile, notes)``."""
    require_administrator(actor)
    active = bool(active)
    with transaction.atomic():
        profile = StaffProfile.objects.select_related("user").select_for_update().get(pk=profile.pk)
        if not active and profile.user_id == actor.pk:
            raise DomainError({"form": "You can't deactivate your own account."})
        if active and profile.role == Role.HOD:
            department = Department.objects.select_for_update().get(pk=profile.department_id)
            _check_role_rules(Role.HOD, department, profile)
        profile.is_active = active
        profile.save(update_fields=["is_active"])
        user = profile.user
        user.is_active = active
        user.save(update_fields=["is_active"])
        notes = _sync_department_head(profile)
    logger.info("audit action=STAFF_ACTIVE active=%s id=%s by=%s", active, profile.pk, actor.pk)
    return profile, notes


def reset_staff_password(*, profile, data, actor):
    """The administrator sets a new password. Existing sessions stop working."""
    require_administrator(actor)
    password = _clean_password(data, profile.user)
    user = profile.user
    user.set_password(password)
    user.save(update_fields=["password"])
    logger.info("audit action=STAFF_PASSWORD_RESET id=%s by=%s", profile.pk, actor.pk)
    return profile
