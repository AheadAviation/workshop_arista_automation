"""REST API views for netbox_demo (INIT-001/SPEC-002).

Two kinds of endpoint:

* Model CRUD through `NetBoxModelViewSet`, which inherits NetBox's token auth and
  object permissions.
* A small set of named actions, each mapped to a fixed lab-agent action. They are
  POST-only and permission-checked, so the API cannot be used to do anything the
  UI cannot.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from netbox.api.viewsets import NetBoxModelViewSet

from .. import assurance, conf, day2, design, intent, lab, renderer
from ..models import LabAgent, LabJob, StagedChange
from .serializers import LabAgentSerializer, LabJobSerializer, StagedChangeSerializer


class LabAgentViewSet(NetBoxModelViewSet):
    queryset = LabAgent.objects.all()
    serializer_class = LabAgentSerializer


class LabJobViewSet(NetBoxModelViewSet):
    queryset = LabJob.objects.all()
    serializer_class = LabJobSerializer


class StagedChangeViewSet(NetBoxModelViewSet):
    queryset = StagedChange.objects.all()
    serializer_class = StagedChangeSerializer


class HealthView(APIView):
    """Reports whether this plugin can actually reach a lab, and why not."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        configured, problem = conf.is_configured()
        health = lab.health_or_none() if configured else None
        return Response(
            {
                "plugin": "netbox_demo",
                "configured": configured,
                "config_problem": problem,
                "lab_agent_url": conf.lab_agent_url(),
                "lab_reachable": health is not None,
                "lab": health or {},
                "devices_expected": list(design.ALL_DEVICES),
                "devices_documented": [d.name for d in intent.demo_devices()],
                "assurance": assurance.summary(),
            },
            status=status.HTTP_200_OK if configured else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class FabricIntentView(APIView):
    """The rendered configuration for every device, or for one with `?device=`."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        device = request.query_params.get("device", "")
        missing = intent.missing_devices()
        if missing:
            return Response(
                {
                    "error": "not every device is documented in NetBox yet",
                    "missing": missing,
                },
                status=status.HTTP_409_CONFLICT,
            )

        configs = renderer.render_all(intent.intent_for_all(), **intent.render_kwargs())
        problems = renderer.sanity_check(configs)
        if device:
            if device not in configs:
                return Response(
                    {"error": f"unknown device {device!r}", "devices": sorted(configs)},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(
                {"device": device, "config": configs[device], "problems": problems}
            )
        return Response({"devices": sorted(configs), "configs": configs, "problems": problems})


class DriftView(APIView):
    """Live drift for one device: rendered intent compared with `show running-config`."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        device = request.query_params.get("device", "")
        if device not in design.ALL_DEVICES:
            return Response(
                {"error": "device is required", "devices": list(design.ALL_DEVICES)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            live = lab.eapi_show(device, list(day2.DRIFT_READ_COMMANDS))
        except lab.LabAgentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        rows = live.get("result") or []
        running = ""
        if rows and isinstance(rows, list) and isinstance(rows[0], dict):
            running = rows[0].get("output", "")

        rendered = renderer.render_device(intent.intent_for(device), **intent.render_kwargs())
        items = day2.detect_drift(device, rendered, running or "")
        return Response(
            {
                "device": device,
                "drift": [item.__dict__ for item in items],
                "summary": day2.summarize_drift(items),
                "assurance": assurance.summary(),
            }
        )


class LabActionView(APIView):
    """Invoke one allowlisted lab action, recording it as a `LabJob`."""

    permission_classes = [IsAuthenticated]

    ALLOWED = set(lab.AGENT_ACTIONS)

    def post(self, request, action: str):
        if action not in self.ALLOWED:
            return Response(
                {"error": f"unknown action {action!r}", "allowed": sorted(self.ALLOWED)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        job = LabJob.objects.create(
            agent=LabAgent.objects.filter(enabled=True).first(),
            action=action,
            status=LabJob.Status.RUNNING,
            payload=request.data if isinstance(request.data, dict) else {},
        )
        try:
            result = lab.run_action(action, request.data if isinstance(request.data, dict) else {})
        except lab.LabAgentError as exc:
            job.status = LabJob.Status.FAILED
            job.error = str(exc)
            job.save(update_fields=["status", "error"])
            return Response(
                {"job": job.pk, "error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )

        job.status = LabJob.Status.COMPLETED
        job.output = str(result.get("output", ""))[:20000]
        job.save(update_fields=["status", "output"])
        return Response({"job": job.pk, "status": job.status, "result": result})
