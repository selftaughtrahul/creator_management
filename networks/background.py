import logging
import threading

from django.db import close_old_connections

logger = logging.getLogger("networks")

_guard = threading.Lock()
_inflight = set()


def run_in_background(key, task):
    """Start one background sync for this key. A second start while it runs does nothing."""
    with _guard:
        if key in _inflight:
            return False
        _inflight.add(key)

    def wrapped():
        close_old_connections()
        try:
            task()
        except Exception:
            logger.exception("background_sync_failed key=%s", key)
        finally:
            with _guard:
                _inflight.discard(key)
            close_old_connections()

    threading.Thread(target=wrapped, daemon=True, name=f"sync-{key}").start()
    return True
