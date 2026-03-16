"""Lambda handler for desk-reaper. Stops overdue workstations."""

import logging
import traceback

from desk.aws import reap_overdue

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

        return {"stopped": stopped}
    except Exception:
        logger.exception("Reaper failed: %s", traceback.format_exc())
        raise
