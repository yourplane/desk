"""Lambda handler for desk-reaper. Stops overdue workstations and reconciles router infra."""

import logging
import traceback

from desk.aws import reap_overdue
from desk.router_infra import reconcile_router_infra

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    try:
        overdue = reap_overdue()

        stopped = [
            {"instance_id": w.instance_id, "name": w.name, "shutdown_at": w.shutdown_at}
            for w in overdue
        ]

        if stopped:
            logger.info("Stopped %d workstation(s): %s", len(stopped), stopped)
        else:
            logger.info("No overdue workstations.")

        router_result = reconcile_router_infra()
        logger.info("Router infra reconcile: %s", router_result)

        return {"stopped": stopped, "router_infra": router_result}
    except Exception:
        logger.exception("Reaper failed: %s", traceback.format_exc())
        raise
