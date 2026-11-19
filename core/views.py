from django.shortcuts import render, redirect, get_object_or_404
from .models import Movie, Show, Reservation, Seat, MasterReservation, Cinema
from django.utils import timezone
from .forms import RegisterForm
from django.contrib.auth.models import User
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.db.models import Exists, OuterRef
from django.contrib import messages
from django.conf import settings
from .services import (
    PaymentError,
    SeatUnavailable,
    release_expired_reservations,
    send_receipt_email,
    start_checkout,
    verify_payment,
)
from django.http import HttpResponse
from django.template.loader import render_to_string
from collections import OrderedDict
from accounts.models import Profile


@login_required
def reservation_detail(request, pk):
    # .distinct(): a multi-seat booking would otherwise match once per seat
    paid_by_user = MasterReservation.objects.filter(
        payment_status="Completed", reservations__customer=request.user
    ).distinct()
    master = get_object_or_404(paid_by_user, pk=pk)
    context = {
        'master': master,
        'seats': master.reservations.values_list('seat__name', flat=True),
        'amount': master.amount / 100,
    }
    return render(request, "core/reservation-detail.html", context)


@login_required
def reservations(request):
    # Only the current user's paid bookings, newest first
    master_reservations = (
        MasterReservation.objects
        .filter(payment_status="Completed", reservations__customer=request.user)
        .select_related("show__movie")
        .distinct()
        .order_by("-id")
    )
    return render(request, "core/reservations.html", {'master_reservations': master_reservations})


@login_required
def verify_reservation_payment(request):
    """Khalti sends the customer back here after paying (or cancelling)."""
    try:
        master, outcome = verify_payment(request.GET.get('pidx'))
    except PaymentError as e:
        messages.error(request, str(e))
        return redirect("movie_list")

    if outcome == "confirmed":
        send_receipt_email(request, master)
        messages.success(request, "Payment done and reservations confirmed")
        return redirect("reservations")

    if outcome == "already_confirmed":
        return redirect("reservations")

    if outcome == "paid_but_released":
        messages.error(
            request,
            f"Your payment went through but the seat hold had expired. "
            f"Please contact support with reference {master.transaction_id}.",
        )
    else:
        messages.error(request, "Payment was not completed, so the seats were not booked.")
    return redirect("movie_list")


@login_required
def hall_seats_view(request, show_id):
    show = get_object_or_404(Show, pk=show_id)

    if request.method == "POST":
        seat_ids = [i for i in request.POST.get('seats', '').split(',') if i.isdigit()]
        seats = list(Seat.objects.filter(pk__in=seat_ids))

        try:
            if len(seats) != len(set(seat_ids)):
                raise SeatUnavailable("Incorrect seat selection.")
            payment_url = start_checkout(request, show, seats)
        except (SeatUnavailable, PaymentError) as e:
            messages.error(request, str(e))
            return redirect("hall_seats", show_id=show.id)

        messages.success(
            request,
            f"Seats reserved for {settings.RESERVATION_WINDOW_TIME} minutes. "
            "Please confirm payment within given time",
        )
        return redirect(payment_url)

    release_expired_reservations()
    seats = show.cinemahall.seats.annotate(
        reserved=Exists(
            Reservation.objects.filter(
                seat=OuterRef("pk"),
                show=show,
                status__in=[
                    Reservation.STATUS_CHOICES.confirmed,
                    Reservation.STATUS_CHOICES.pending,
                ],
            )
        )
    )
    seats = sorted(seats, key=lambda seat: (seat.name[0], int(seat.name[1:])))
    return render(request, "core/hall_seats.html", {'show': show, 'seats': seats})


def home(request):
    
    latest_movies = Movie.objects.order_by("-release_date")[:8]
    # print(latest_movies)
    
    context = {
        'movies': latest_movies,
    }
    
    return render(request, "core/home.html", context)


def get_shows_partial(request):
    cinema_id = request.GET.get('cinema')
    movie_id = request.GET.get('movie_id')

    if not cinema_id or not movie_id:
        shows_by_date = OrderedDict()
    else:
        shows = (
            Show.objects
            .filter(
                movie_id=movie_id,
                show_time__gt=timezone.now(),
            )
            .order_by('show_time')
        )

        shows_by_date = OrderedDict()

        for show in shows:
            # Convert to local time before extracting the date
            local_date = timezone.localtime(show.show_time).date()

            if local_date not in shows_by_date:
                shows_by_date[local_date] = []

            shows_by_date[local_date].append(show)

    context = {
        'shows_by_date': shows_by_date,
        'from_date': timezone.now(),
    }

    return HttpResponse(
        render_to_string(
            "core/shows-partial.html",
            context
        )
    )
    
    
def movie_detail(request, pk):
    movie = Movie.objects.get(pk=pk)
    cinemas = Cinema.objects.all()
    # print(shows[0].show_time, timezone.now())
    context = {
        'movie': movie,
        'cinemas':cinemas,
        "from_date": timezone.now(),
    }
    return render(request, "core/movie_detail.html", context)
    

def register_view(request):
    if request.method == "POST":
        # print("REquest data: ", request.POST)
        form = RegisterForm(data=request.POST)
        
        if form.is_valid():
            data = form.cleaned_data
            user = User.objects.create_user(
                username=data.get('username'),
                email=data.get('email'),
                password=data.get('password'),
                first_name=data.get('first_name'),
                last_name=data.get('last_name')
            )
            
            Profile.objects.create(
                user=user
            )
            print("User creation successful")
            return redirect("login")
        else:
            return render(request, "core/register.html", {
                'form':form
            })
    
        
    return render(request, "core/register.html")


def login_view(request):
    
    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        # print(username, password)
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, "Login Successful")
            return redirect("movie_list")         
        messages.error(request, "Invalid Credentials")
        return redirect("login")
    
    return render(request, "core/login.html")
