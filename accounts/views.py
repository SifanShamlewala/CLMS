from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods, require_POST

@login_required
def dashboard_view(request):
    return render(request, "accounts/dashboard.html")


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        # TODO 1: if username or password is empty, re-render the page with an error
        if not username or not password:
            return render(
                request,
                "accounts/login.html",
                {"error": "Both username and password are required.", "username": username}
            )
        # TODO 2: user = authenticate(request, username=username, password=password)
        user = authenticate(request, username=username, password=password)
        # TODO 3: if user is None, re-render with ONE generic error
        #         ("Invalid username or password") for every failure case
        if user is None:
            return render(
                request,
                "accounts/login.html",
                {"error": "Invalid username or password.", "username": username}
            )
        # TODO 4: login(request, user) and redirect to "dashboard"
        login(request, user)
        return redirect("dashboard")
    return render(request, "accounts/login.html")


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")