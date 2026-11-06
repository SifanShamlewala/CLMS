from django.urls import path

from . import views

urlpatterns = [
    path("setup/leave-types/", views.leave_type_list, name="leave_type_list"),
    path("setup/leave-types/new/", views.leave_type_create, name="leave_type_create"),
    path("setup/leave-types/<int:pk>/edit/", views.leave_type_edit, name="leave_type_edit"),
    path("setup/leave-types/<int:pk>/toggle/", views.leave_type_toggle, name="leave_type_toggle"),
]
