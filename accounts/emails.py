from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.utils import timezone

from .models import OTP


def _new_otp_for(username):
    """Create a fresh OTP for `username` (and tidy up expired ones). None if no such user."""
    OTP.objects.filter(expires_at__lte=timezone.now()).delete()
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return None, None
    return user, OTP.objects.create(user=user)


def send_password_reset_mail(username, reset_url):
    user, otp = _new_otp_for(username)
    if user is None or not user.email:
        return

    send_mail(
        subject="Email Verification",
        message=f"""Please follow the link and continue resetting password
        {reset_url}

        OTP: {otp.value}
        """,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )


def send_email_verification_mail(username, verify_url):
    user, otp = _new_otp_for(username)
    if user is None or not user.email:
        return

    send_mail(
        subject="Email Verification",
        message=f"""Please verify your email with link given below and given OTP {otp.value}
                    Click on this link to verify. {verify_url}
        """,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )
