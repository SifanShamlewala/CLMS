from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    #Designations
    path("setup/designations/", views.designation_list, name="designation_list"),
    path("setup/designations/new/", views.designation_create, name="designation_create"),
    path("setup/designations/<int:pk>/edit/", views.designation_edit, name="designation_edit"),
    path("setup/designations/<int:pk>/toggle/", views.designation_toggle, name="designation_toggle"),
    # Departments
    path("setup/departments/", views.department_list, name="department_list"),
    path("setup/departments/new/", views.department_create, name="department_create"),
    path("setup/departments/<int:pk>/edit/", views.department_edit, name="department_edit"),
    path("setup/departments/<int:pk>/toggle/", views.department_toggle, name="department_toggle"),
]
