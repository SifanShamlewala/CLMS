from django.urls import path

from . import application_views, views

urlpatterns = [
    # Setup: balances
    path("setup/balances/", views.balance_list, name="balance_list"),
    path("setup/balances/new/", views.balance_create, name="balance_create"),
    path("setup/balances/<int:pk>/edit/", views.balance_edit, name="balance_edit"),
    # Leave applications
    path("leaves/apply/", application_views.leave_apply, name="leave_apply"),
    path("leaves/mine/", application_views.leave_mine, name="leave_mine"),
    path("leaves/<int:pk>/", application_views.leave_detail, name="leave_detail"),
]
