from django.urls import path

from . import views

urlpatterns = [
    path("setup/balances/", views.balance_list, name="balance_list"),
    path("setup/balances/new/", views.balance_create, name="balance_create"),
    path("setup/balances/<int:pk>/edit/", views.balance_edit, name="balance_edit"),
]
