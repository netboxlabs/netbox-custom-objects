import threading
from functools import wraps


def thread_safe_model_generation(func):
    """
    Decorator to ensure thread-safe model generation.

    This decorator prevents race conditions when multiple threads try to generate
    the same custom object model simultaneously. It uses per-model locks to ensure
    only one thread can generate a specific model at a time, while allowing
    different models to be generated concurrently and preventing deadlocks.
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # Get or create a lock for this specific model
        with self._global_lock:
            if self.id not in self._model_cache_locks:
                self._model_cache_locks[self.id] = threading.RLock()
            model_lock = self._model_cache_locks[self.id]

        # Use the per-model lock for thread safety
        with model_lock:
            return func(self, *args, **kwargs)
    return wrapper
