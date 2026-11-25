"""Plugin models (INIT-001/SPEC-002).

Four models, each existing for a reason:

* `LabAgent`     — a lab host you can point the plugin at, so switching hosts is a
                   UI action rather than a NetBox restart.
* `LabJob`       — one record per action, written before the call and updated
                   after, so the page shows the truth after a refresh and a
                   failure leaves evidence.
* `DemoStep`     — durable demo progress, held in the database rather than a file
                   because NetBox Cloud has no writable local filesystem.
* `StagedChange` — a Day 2 preview awaiting approval, in the database for the
                   same reason (NetBox Cloud runs several worker processes).
"""

from __future__ import annotations

from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel


class LabAgent(NetBoxModel):
    """A local lab host the plugin can drive, reached over a private network."""

    name = models.CharField(max_length=100, unique=True)
    url = models.URLField(help_text="Base URL of lab-agent, e.g. http://100.89.183.114:8085")
    token = models.CharField(
        max_length=200,
        help_text="Bearer token; must match LAB_AGENT_TOKEN on the lab host",
    )
    enabled = models.BooleanField(default=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=50, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "lab agent"
        verbose_name_plural = "lab agents"

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("plugins:netbox_demo:labagent", args=[self.pk])


class LabJob(NetBoxModel):
    """One lab action, recorded before it runs so a failure is never invisible."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    agent = models.ForeignKey(
        LabAgent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
    action = models.CharField(max_length=60)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payload = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    output = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created",)
        verbose_name = "lab job"
        verbose_name_plural = "lab jobs"
        indexes = [
            models.Index(fields=["action", "-created"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} ({self.status})"

    def get_absolute_url(self) -> str:
        return reverse("plugins:netbox_demo:labjob", args=[self.pk])

    def get_status_color(self) -> str:
        return {
            self.Status.PENDING: "secondary",
            self.Status.RUNNING: "info",
            self.Status.COMPLETED: "success",
            self.Status.FAILED: "danger",
        }.get(self.Status(self.status), "secondary")

    @property
    def is_finished(self) -> bool:
        return self.status in (self.Status.COMPLETED, self.Status.FAILED)


class DemoStep(NetBoxModel):
    """Durable demo progress: one row per step, keyed by a stable slug."""

    key = models.CharField(max_length=60, unique=True)
    label = models.CharField(max_length=120)
    done = models.BooleanField(default=False)
    detail = models.CharField(max_length=255, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("key",)
        verbose_name = "demo step"
        verbose_name_plural = "demo steps"

    def __str__(self) -> str:
        return f"{self.label}: {'done' if self.done else 'not done'}"


class StagedChange(NetBoxModel):
    """A Day 2 change previewed but not applied, awaiting a named approver."""

    change_id = models.CharField(max_length=40, unique=True)
    operation = models.CharField(max_length=60)
    device = models.CharField(max_length=100, blank=True)
    values = models.JSONField(default=dict, blank=True)
    netbox_plan = models.JSONField(default=dict, blank=True)
    eos_commands = models.JSONField(default=dict, blank=True)
    preview = models.TextField(blank=True)

    class Meta:
        ordering = ("-created",)
        verbose_name = "staged change"
        verbose_name_plural = "staged changes"
        indexes = [models.Index(fields=["-created"])]

    def __str__(self) -> str:
        return f"{self.operation} {self.change_id}"
