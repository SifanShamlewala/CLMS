from django.urls import path, include

urlpatterns = [
    path("", include("accounts.urls")),
    path("", include("academics.urls")),
    path("", include("leaves.urls")),
]
