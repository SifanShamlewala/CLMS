from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect,render
from django.views.decorators.http import require_http_methods, require_POST

from core.permissions import administrator_required
from core.validation import DomainError

from . import services
from .models import LeaveType

FORM = "academics/leavetype_form.html"

@administrator_required
@require_http_methods(["GET"])
def leave_type_list(request):
    leave_types = LeaveType.objects.order_by("name")
    return render(request, "academics/leavetype_list.html", {"leave_types": leave_types})

@administrator_required
@require_http_methods(["GET","POST"])
def leave_type_create(request):
    if request.method == "POST":
        try:
            leave_type = services.create_leave_type(data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, FORM, {"mode": "create", "errors": exc.errors, "values": request.POST})
        messages.success(request, f"Leave type “{leave_type.name}” created.")
        return redirect("leave_type_list")
    return render(request, FORM, {"mode": "create", "errors":{}, "values": {}})

@administrator_required
@require_http_methods(["GET","POST"])
def leave_type_edit(request, pk):
    leave_type = get_object_or_404(LeaveType, pk=pk)
    if request.method == "POST":
        try:
            services.update_leave_type(leave_type=leave_type, data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, FORM, {"mode": "edit", "leave_type": leave_type, "errors": exc.errors, "values": request.POST})
        messages.success(request,  f"Leave type “{leave_type.name}” updated.")
        return redirect("leave_type_list")
    return render(request, FORM, {"mode": "edit", "leave_type": leave_type, "errors":{},
                                  "values": {"name": leave_type.name, "code": leave_type.code, "description": leave_type.description}})

@administrator_required
@require_POST
def leave_type_toggle(request, pk):
    leave_type = get_object_or_404(LeaveType, pk=pk)
    active = request.POST.get("active") =="1"
    services.set_leave_type_active(leave_type=leave_type, active=active, actor=request.user)
    messages.success(
        request, f"Leave type “{leave_type.name}” {'activated' if active else 'deactivated'}."
    )
    return redirect("leave_type_list")