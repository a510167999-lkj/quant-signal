import os
from contextlib import contextmanager
from threading import RLock


_proxy_env_lock = RLock()


@contextmanager
def market_data_proxy_scope():
    """AKShare public endpoints are usually more reliable without local HTTP proxies."""
    if os.getenv("MARKET_DATA_USE_PROXY", "").strip() == "1":
        yield
        return

    proxy_keys = ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]
    with _proxy_env_lock:
        saved = {key: os.environ.get(key) for key in proxy_keys}
        for key in proxy_keys:
            os.environ.pop(key, None)
        try:
            yield
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
