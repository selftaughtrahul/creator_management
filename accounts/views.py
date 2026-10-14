import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from allauth.account.forms import LoginForm

from accounts.forms import PeopleForm, ProfileForm
from accounts.models import DEFAULT_EMPLOYEE_PERMISSIONS, Notification, StudioProfile
from accounts.permissions import home_url, is_creator, is_staff_role, superuser_required


def home(request):
    if request.user.is_authenticated:
        return redirect(home_url(request.user))
    return render(request, "marketing/home.html")


def privacy(request):
    return render(request, "marketing/privacy.html")


def data_deletion(request):
    return render(request, "marketing/data_deletion.html")


def employee_join(request):
    raise Http404


def signup_closed(request):
    return render(request, "accounts/signup_closed.html", status=403)


def creator_login(request):
    if request.user.is_authenticated:
        return redirect(home_url(request.user))
    form = LoginForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        if not is_creator(form.user):
            form.add_error(None, "Staff accounts use the staff log in. This page is for creators.")
        else:
            form.login(request)
            return redirect(home_url(request.user))
    return render(request, "account/login.html", {"form": form})


def staff_login(request):
    if request.user.is_authenticated:
        return redirect(home_url(request.user))
    form = LoginForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        if not is_staff_role(form.user):
            form.add_error(None, "This log in is for staff. Creators register and log in from the main page.")
        else:
            form.login(request)
            return redirect(home_url(request.user))
    return render(request, "accounts/staff_login.html", {"form": form})


@login_required
@require_POST
def set_theme(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = request.POST
    theme = payload.get("theme")
    if theme not in StudioProfile.Theme.values:
        return JsonResponse({"ok": False}, status=400)
    profile, _ = StudioProfile.objects.get_or_create(user=request.user)
    profile.theme = theme
    profile.save(update_fields=["theme"])
    return JsonResponse({"ok": True, "theme": theme})


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile saved.")
        return redirect("accounts:profile")
    return render(
        request,
        "app/profile.html",
        {"form": form, "open_drawer": "accountDrawer" if request.method == "POST" else ""},
    )


@login_required
def notifications(request):
    rows = request.user.notifications.all()
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    return render(request, "accounts/notifications.html", {"page": page})


@login_required
@require_POST
def mark_notification_read(request, pk):
    note = get_object_or_404(Notification, pk=pk, user=request.user)
    if note.read_at is None:
        note.read_at = timezone.now()
        note.save(update_fields=["read_at"])
    return redirect(note.url or "accounts:notifications")


@login_required
@require_POST
def mark_all_notifications_read(request):
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    messages.success(request, "Notifications marked read.")
    return redirect("accounts:notifications")


def _people_page(request, *, person_form=None, person=None, open_drawer=""):
    rows = StudioProfile.objects.select_related("user").order_by("role", "user__email")
    role = request.GET.get("role")
    if role:
        rows = rows.filter(role=role)
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "accounts/people.html",
        {
            "page": page,
            "role": role,
            "person_form": person_form,
            "person": person,
            "open_drawer": open_drawer,
        },
    )


@superuser_required
def people(request):
    return _people_page(request)


@superuser_required
def person_detail(request, pk):
    profile = get_object_or_404(StudioProfile.objects.select_related("user"), pk=pk)
    initial = {
        "role": profile.role,
        "permissions": profile.permissions or list(DEFAULT_EMPLOYEE_PERMISSIONS),
        "is_active": profile.user.is_active,
    }
    form = PeopleForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        role = form.cleaned_data["role"]
        if profile.user_id == request.user.pk and role != StudioProfile.Role.ADMIN:
            messages.error(request, "You cannot remove your own admin role.")
            return redirect("accounts:person", pk=pk)
        profile.role = role
        if role == StudioProfile.Role.EMPLOYEE:
            profile.permissions = form.cleaned_data.get("permissions") or list(DEFAULT_EMPLOYEE_PERMISSIONS)
        else:
            profile.permissions = []
        profile.save(update_fields=["role", "permissions"])
        user = profile.user
        user.is_active = form.cleaned_data.get("is_active", True)
        user.save(update_fields=["is_active"])
        if role == StudioProfile.Role.CREATOR:
            from creators.models import CreatorProfile

            CreatorProfile.objects.get_or_create(
                user=user,
                defaults={"display_name": user.get_full_name() or user.email},
            )
        messages.success(request, "Person updated.")
        return redirect("accounts:people")
    return _people_page(request, person_form=form, person=profile, open_drawer="personDrawer")
