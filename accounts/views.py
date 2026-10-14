from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.forms import EmployeeJoinForm, PeopleForm, ProfileForm
from accounts.models import DEFAULT_EMPLOYEE_PERMISSIONS, Notification, StudioProfile
from accounts.permissions import admin_required, home_url
from accounts.utils import unique_username

User = get_user_model()


def home(request):
    if request.user.is_authenticated:
        return redirect(home_url(request.user))
    return render(request, "marketing/home.html")


def privacy(request):
    return render(request, "marketing/privacy.html")


def data_deletion(request):
    return render(request, "marketing/data_deletion.html")


def employee_join(request):
    if request.user.is_authenticated:
        return redirect(home_url(request.user))
    form = EmployeeJoinForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        name = form.cleaned_data["full_name"].strip()
        first, _, last = name.partition(" ")
        user = User.objects.create_user(
            username=unique_username(email),
            email=email,
            password=form.cleaned_data["password1"],
            first_name=first,
            last_name=last,
        )
        StudioProfile.objects.update_or_create(
            user=user,
            defaults={
                "role": StudioProfile.Role.EMPLOYEE,
                "permissions": list(DEFAULT_EMPLOYEE_PERMISSIONS),
            },
        )
        from allauth.account.models import EmailAddress

        address = EmailAddress.objects.create(user=user, email=email, primary=True, verified=False)
        address.send_confirmation(request, signup=True)
        messages.info(request, "Check your email to verify the address before logging in.")
        return redirect("account_email_verification_sent")
    return render(request, "accounts/employee_join.html", {"form": form})


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile saved.")
        return redirect("accounts:profile")
    return render(request, "app/profile.html", {"form": form})


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


@admin_required
def people(request):
    rows = StudioProfile.objects.select_related("user").order_by("role", "user__email")
    role = request.GET.get("role")
    if role:
        rows = rows.filter(role=role)
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    return render(request, "accounts/people.html", {"page": page, "role": role})


@admin_required
def person_detail(request, pk):
    profile = get_object_or_404(StudioProfile.objects.select_related("user"), pk=pk)
    initial = {
        "role": profile.role if profile.role != StudioProfile.Role.TEAM else StudioProfile.Role.ADMIN,
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
    return render(request, "accounts/person.html", {"form": form, "profile": profile})
