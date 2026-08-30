from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.issues.models import Bounty
from django.db import transaction

class BountyViewSet(viewsets.ReadOnlyModelViewSet):
    def get_object(self):
        # mock implementation
        return Bounty.objects.get(pk=self.kwargs['pk'])

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAuthenticated])
    def claim(self, request, pk=None):
        bounty = self.get_object()
        if bounty.status != Bounty.Status.OPEN:
            return Response(
                {"error": "Bounty is not open for claiming."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        bounty.status = Bounty.Status.CLAIMED
        bounty.claimed_by = request.user
        bounty.save()

        return Response({"status": "Bounty claimed successfully!"})
