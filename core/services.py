"""
Everything about booking + paying lives here, so the website views and the
REST API share exactly one implementation.

Flow
----
1. start_checkout()  -> holds the seats (pending) and opens a Khalti payment
2. user pays on Khalti and is sent back to our "verification" URL
3. verify_payment()  -> asks Khalti if the payment really completed and, if so,
                        confirms the seats. Safe to call more than once.

Unpaid seats are released lazily by release_expired_reservations(), so no
background worker (Celery/Redis) is needed.
"""
import logging
from io import BytesIO
from email.mime.image import MIMEImage

import qrcode
import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import MasterReservation, Reservation

logger = logging.getLogger(__name__)

KHALTI_TIMEOUT = 15  # seconds

PENDING = "Pending"
COMPLETED = "Completed"
EXPIRED = "Expired"
FAILED = "Failed"


class SeatUnavailable(Exception):
    """The chosen seats can't be booked (taken, wrong hall, show started...)."""


class PaymentError(Exception):
    """Khalti couldn't be reached or rejected the request."""


# --------------------------------------------------------------------------
# Khalti
# --------------------------------------------------------------------------
def _khalti_post(endpoint, payload):
    if not settings.KHALTI_API_SECRET_KEY:
        raise PaymentError("Payments are not configured (KHALTI_API_SECRET_KEY is empty).")

    try:
        response = requests.post(
            f"{settings.KHALTI_BASE_URL}{endpoint}/",
            json=payload,
            headers={"Authorization": f"key {settings.KHALTI_API_SECRET_KEY}"},
            timeout=KHALTI_TIMEOUT,
        )
        data = response.json()
    except (requests.RequestException, ValueError):
        logger.exception("Khalti %s request failed", endpoint)
        raise PaymentError("Could not reach Khalti. Please try again.")

    if response.status_code != 200:
        logger.warning("Khalti %s returned %s: %s", endpoint, response.status_code, data)
        raise PaymentError("Khalti rejected the payment request.")

    return data


# --------------------------------------------------------------------------
# Releasing seats
# --------------------------------------------------------------------------
def release_expired_reservations():
    """Free seats whose payment window has passed without a completed payment."""
    expired = Reservation.objects.filter(
        status=Reservation.STATUS_CHOICES.pending,
        expires_at__lte=timezone.now(),
    )
    master_ids = {pk for pk in expired.values_list("master_reservation_id", flat=True) if pk}

    with transaction.atomic():
        # Keep the master row (so a late Khalti callback can still find it),
        # just mark it expired and free the seats.
        MasterReservation.objects.filter(pk__in=master_ids).exclude(
            payment_status=COMPLETED
        ).update(payment_status=EXPIRED)
        expired.delete()
        Reservation.objects.filter(status=Reservation.STATUS_CHOICES.cancel).delete()


def _release(master, status):
    """Drop a master's unpaid seats right away (payment failed / cancelled)."""
    with transaction.atomic():
        master.reservations.filter(status=Reservation.STATUS_CHOICES.pending).delete()
        if master.payment_status != COMPLETED:
            master.payment_status = status
            master.save(update_fields=["payment_status"])


# --------------------------------------------------------------------------
# Step 1: reserve seats + open payment
# --------------------------------------------------------------------------
def start_checkout(request, show, seats):
    """
    Hold `seats` for `show` and start a Khalti payment.
    Returns the Khalti payment URL to send the customer to.
    Raises SeatUnavailable or PaymentError (seats are released on failure).
    """
    seats = list(seats)
    if not seats:
        raise SeatUnavailable("Please select at least one seat.")
    if show.show_time <= timezone.now():
        raise SeatUnavailable("This show has already started.")
    if any(seat.cinemahall_id != show.cinemahall_id for seat in seats):
        raise SeatUnavailable("Incorrect seat selection.")

    release_expired_reservations()

    amount = show.price * len(seats) * 100  # Khalti uses paisa
    try:
        with transaction.atomic():
            master = MasterReservation.objects.create(
                show=show, amount=amount, payment_status=PENDING
            )
            for seat in seats:
                Reservation.objects.create(
                    master_reservation=master,
                    show=show,
                    seat=seat,
                    customer=request.user,
                )
    except IntegrityError:
        raise SeatUnavailable("One of these seats has just been reserved by another customer.")

    payload = {
        "return_url": request.build_absolute_uri(reverse("verify_reservation_payment")),
        "website_url": request.build_absolute_uri("/"),
        "amount": amount,
        "purchase_order_id": str(master.id),
        "purchase_order_name": f"{show.movie.name} ticket(s)",
    }
    customer = {
        "name": request.user.get_full_name(),
        "email": request.user.email,
    }
    customer = {key: value for key, value in customer.items() if value}
    if customer:
        payload["customer_info"] = customer

    try:
        data = _khalti_post("initiate", payload)
        pidx, payment_url = data.get("pidx"), data.get("payment_url")
        if not (pidx and payment_url):
            raise PaymentError("Khalti did not return a payment link.")
    except PaymentError:
        _release(master, FAILED)  # don't leave seats locked for nothing
        raise

    master.pidx = pidx
    master.save(update_fields=["pidx"])
    return payment_url


# --------------------------------------------------------------------------
# Step 3: confirm payment
# --------------------------------------------------------------------------
def verify_payment(pidx):
    """
    Ask Khalti about `pidx` (never trust the query-string the browser sends).
    Returns (master, outcome) where outcome is one of:
      "confirmed"          payment OK, seats confirmed (send the receipt)
      "already_confirmed"  we already processed this payment
      "paid_but_released"  paid, but the seat hold had expired and been released
      "not_paid"           cancelled / expired / still pending at Khalti
    Raises PaymentError for unknown references, Khalti errors or amount mismatch.
    """
    master = MasterReservation.objects.filter(pidx=pidx).first() if pidx else None
    if master is None:
        raise PaymentError("Unknown payment reference.")

    if master.payment_status == COMPLETED:
        return master, "already_confirmed"

    data = _khalti_post("lookup", {"pidx": pidx})
    status = data.get("status")

    if status != "Completed":
        if status in ("User canceled", "Expired"):
            _release(master, status)
        return master, "not_paid"

    if int(data.get("total_amount", 0)) != master.amount:
        logger.error("Amount mismatch for %s: khalti=%s ours=%s", master.id, data.get("total_amount"), master.amount)
        raise PaymentError("Payment amount mismatch. Please contact support.")

    with transaction.atomic():
        master.transaction_id = data.get("transaction_id")
        master.payment_status = COMPLETED
        master.save(update_fields=["transaction_id", "payment_status"])
        master.reservations.filter(
            status=Reservation.STATUS_CHOICES.pending
        ).update(status=Reservation.STATUS_CHOICES.confirmed)

    if not master.reservations.exists():
        logger.error("Paid but seats were released: master=%s txn=%s", master.id, master.transaction_id)
        return master, "paid_but_released"

    return master, "confirmed"


# --------------------------------------------------------------------------
# Receipt email (sent inline; failures are logged, never block the booking)
# --------------------------------------------------------------------------
def send_receipt_email(request, master):
    first = master.reservations.select_related("customer").first()
    if first is None or not first.customer.email:
        return
    user = first.customer

    try:
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(str(master.id))
        qr.make(fit=True)
        buffer = BytesIO()
        qr.make_image().save(buffer, format="PNG")
        qr_png = buffer.getvalue()

        html = render_to_string(
            "email/reservation-success-mail.html",
            {
                "full_name": user.get_full_name() or user.username,
                "reservation_detail_url": request.build_absolute_uri(
                    reverse("reservation_detail", kwargs={"pk": master.id})
                ),
                "qr_code_cid": "reservation_qr",
            },
        )

        email = EmailMultiAlternatives(
            subject="Reservation Success",
            body="",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        email.attach_alternative(html, "text/html")

        inline_qr = MIMEImage(qr_png, _subtype="png")
        inline_qr.add_header("Content-ID", "<reservation_qr>")
        inline_qr.add_header("Content-Disposition", "inline", filename=f"reservation-{master.id}-qr.png")
        email.attach(inline_qr)
        email.attach(filename=f"reservation-{master.id}-qr.png", content=qr_png, mimetype="image/png")

        email.send(fail_silently=False)
    except Exception:
        logger.exception("Could not send receipt for %s", master.id)
