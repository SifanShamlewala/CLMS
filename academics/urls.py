from django.urls import path

from . import views

urlpatterns = [
    # Leave types
    path("setup/leave-types/", views.leave_type_list, name="leave_type_list"),
    path("setup/leave-types/new/", views.leave_type_create, name="leave_type_create"),
    path("setup/leave-types/<int:pk>/edit/", views.leave_type_edit, name="leave_type_edit"),
    path("setup/leave-types/<int:pk>/toggle/", views.leave_type_toggle, name="leave_type_toggle"),
    # Academic years
    path("setup/academic-years/", views.year_list, name="year_list"),
    path("setup/academic-years/new/", views.year_create, name="year_create"),
    path("setup/academic-years/<int:pk>/edit/", views.year_edit, name="year_edit"),
    path("setup/academic-years/<int:pk>/activate/", views.year_activate, name="year_activate"),
    # Holidays
    path("setup/holidays/", views.holiday_list, name="holiday_list"),
    path("setup/holidays/new/", views.holiday_create, name="holiday_create"),
    path("setup/holidays/<int:pk>/edit/", views.holiday_edit, name="holiday_edit"),
    path("setup/holidays/<int:pk>/delete/", views.holiday_delete, name="holiday_delete"),
    # Policy
    path("setup/policy/", views.policy_edit, name="policy_edit"),
]
