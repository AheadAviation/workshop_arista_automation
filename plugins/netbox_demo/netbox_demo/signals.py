"""Signal handlers for netbox_demo (INIT-001/SPEC-002).

Deliberately almost empty. A plugin that reacts to core NetBox writes is a
plugin that can surprise an operator on someone else's instance, so nothing here
writes to core models. The one signal is for local housekeeping.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

logger = logging.getLogger("netbox_demo")


@receiver(post_delete, sender="netbox_demo.LabAgent")
def clear_agent_from_jobs(sender, instance, **kwargs) -> None:
    """Note the deletion; jobs keep their history with a null agent (SET_NULL)."""
    logger.info("lab agent %s removed; its job history is retained", instance.name)
