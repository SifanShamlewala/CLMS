"""Business logic for designations and departments.

Views never touch the models directly for writes: they call these functions,
which check permission, validate, and write inside a transaction.
"""
import logging

from django.db import InternalError, transaction, IntegrityError

from core.permissions import require_administrator
from core.validation import DomainError, required_text, ensure_unique, validate

from .models import Department, Designation

logger = logging.getLogger("vsclms.setup")

DUPLICATE_TITLE = "A designation with this title already exists."
DUPLICATE_DEPARTMENT = "A department with this name already exists."

# ----------------------------------- designations---------------------------------------

def _clean_designation(data):
    return validate({
        "title": lambda:required_text(data.get("title"), "title", 100, "Title")
    })

def create_designation(*,data, actor):
    require_administrator(actor)
    cleaned = _clean_designation(data)
    try:
        with transaction.atomic():
            ensure_unique(Designation, {"title": cleaned["title"]}, {"title": DUPLICATE_TITLE})
            designation = Designation.objects.create(title=cleaned["title"])
    except IntegrityError:             # a concurrent request created the same title first
        raise DomainError({"title": DUPLICATE_TITLE}) from None
    logger.info("designation created id=%s title=%r by=%s", designation.pk, designation.title, actor.pk)
    return designation

def update_designation(*,designation,data, actor):
    require_administrator(actor)
    cleaned = _clean_designation(data)
    try:
        with transaction.atomic():
            ensure_unique(
                Designation, {"title": cleaned["title"]}, {"title": DUPLICATE_TITLE},
                exclude_pk=designation.pk
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

# ------------------------------------- departments ----------------------------------------------
def _clean_department(data):
    return validate({
        "name": lambda:required_text(data.get("name"), "name", 100, "Name")
    })

def create_department(*,data, actor):
    require_administrator(actor)
    cleaned = _clean_department(data)
    try:
        with transaction.atomic():
            ensure_unique(Department, {"name": cleaned["name"]}, {"name": DUPLICATE_DEPARTMENT})
            department = Department.objects.create(name=cleaned["name"])
    except IntegrityError:
        raise DomainError({"name": DUPLICATE_DEPARTMENT}) from None
    logger.info("department created=%s by=%s", department.pk, department.name, actor.pk)
    return department

def update_department(*,department,data, actor):
    require_administrator(actor)
    cleaned = _clean_department(data)
    try:
        with transaction.atomic():
            ensure_unique(
                Department, {"name": cleaned["name"]}, {"name": DUPLICATE_DEPARTMENT},
                exclude_pk=department.pk
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
                 "form": f"{department.name} still has {remaining} active staff member{'s' if remaining != 1 else ''}. Deactivate or move them before deactivating the department."
                })
            department.is_active = False
            department.save(update_fields=["is_active"])
    else:
        department.is_active = True
        department.save(update_fields=["is_active"])
    logger.info("department active=%s id=%s by=%s", department.is_active, department.pk, actor.pk)
    return department

