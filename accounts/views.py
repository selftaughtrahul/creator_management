from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.forms import ProfileForm


def home(request):
    if request.user.is_authenticated:
        return redirect("analytics:dashboard")
    return render(request, "marketing/home.html")


def privacy(request):
    return render(request, "marketing/privacy.html")


def data_deletion(request):
    return render(request, "marketing/data_deletion.html")


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile saved.")
        return redirect("accounts:profile")
    return render(request, "app/profile.html", {"form": form})
