from django.urls import path

from . import application_views, views, workload_views

urlpatterns = [
    # Setup: balances
    path("setup/balances/", views.balance_list, name="balance_list"),
    path("setup/balances/new/", views.balance_create, name="balance_create"),
    path("setup/balances/<int:pk>/edit/", views.balance_edit, name="balance_edit"),
    # Leave applications
    path("leaves/apply/", application_views.leave_apply, name="leave_apply"),
    path("leaves/mine/", application_views.leave_mine, name="leave_mine"),
    path("leaves/<int:pk>/", application_views.leave_detail, name="leave_detail"),
    # Workload cover
    path("leaves/<int:pk>/workload/assign/", application_views.workload_assign, name="workload_assign"),
    path("workload/", workload_views.workload_inbox, name="workload_inbox"),
    path("workload/<int:pk>/decide/", workload_views.workload_decide, name="workload_decide"),
    path("workload/<int:pk>/remove/", application_views.workload_remove, name="workload_remove"),
]
