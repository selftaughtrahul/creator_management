from django.contrib.auth import get_user_model

User = get_user_model()


def unique_username(email):
    base = (email.split("@")[0][:140] if email else "") or "user"
    username = base
    index = 1
    while User.objects.filter(username=username).exists():
        username = f"{base}{index}"
        index += 1
    return username
