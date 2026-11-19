from core.models import Movie, Cinema
from .serializers import *
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import viewsets
from rest_framework.views import APIView
from rest_framework import permissions, status
from django.conf import settings
from core.services import PaymentError, SeatUnavailable, start_checkout
from .filters import MovieFilter
from rest_framework import filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.throttling import ScopedRateThrottle
from django.core.cache import cache



# @api_view(["GET"])
# def movies(request):
#     movies = Movie.objects.all()
#     serializer = MovieModelSerializer(movies, many=True)
    
#     return Response(serializer.data)

class MovieViewSet(viewsets.ModelViewSet):
    serializer_class = MovieModelSerializer
    queryset = Movie.objects.all()
    filter_backends = [filters.OrderingFilter, DjangoFilterBackend]
    filterset_class = MovieFilter
    ordering_fields = ["name", "release_date"]
    
    def list(self, request, *args, **kwargs):
        cache_key = f"movies:list:{request.get_full_path()}"

        cached_response = cache.get(cache_key)

        if cached_response is not None:
            return Response(cached_response)

        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)

            cache.set(
                cache_key,
                response.data,
                timeout=60,
            )

            return response

        serializer = self.get_serializer(queryset, many=True)

        response = Response(serializer.data)

        cache.set(
            cache_key,
            response.data,
            timeout=60,
        )

        return response    


      
class CinemaViewSet(viewsets.ModelViewSet):
    queryset = Cinema.objects.all()
    
    def get_serializer_class(self):
        if self.action in "retrieve":
            return CinemaModelDetailSerializer
        
        return CinemaModelSerializer
    
    
class CinemaHallViewSet(viewsets.ModelViewSet):
    serializer_class = CinemaHallModelSerializer
    queryset = CinemaHall.objects.all()
    


class ProfileGetView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'profile'
    
    def get(self, request, format=None):
        profile, created = Profile.objects.get_or_create(user=request.user)
        serializer = ProfileSerializer(profile)
        
        return Response(serializer.data)       


class ReserveShowView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ShowReserveSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        show = serializer.validated_data["show"]
        seats = serializer.validated_data["seats"]

        try:
            payment_url = start_checkout(request, show, seats)
        except SeatUnavailable as e:
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PaymentError as e:
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        data = serializer.data
        data["payment_url"] = payment_url
        return Response(
            {
                "status": "success",
                "message": f"Seats reserved for {settings.RESERVATION_WINDOW_TIME} minutes. Please confirm payment within given time",
                "data": data,
            },
            status=status.HTTP_200_OK,
        )
