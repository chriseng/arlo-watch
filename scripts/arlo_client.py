"""Shared Arlo client configuration."""

import os
import shutil
from pathlib import Path

import pyaarlo


def _build_kwargs(*, library_days: int, storage_dir: Path) -> dict:
    kwargs = {
        "username": os.environ["ARLO_USERNAME"],
        "password": os.environ["ARLO_PASSWORD"],
        "library_days": max(library_days, 1),
        "synchronous_mode": True,
        "tfa_source": os.getenv("ARLO_TFA_SOURCE", "console"),
        "tfa_type": os.getenv("ARLO_TFA_TYPE", "email"),
        "storage_dir": str(storage_dir),
    }

    optional_env = {
        "ARLO_TFA_HOST": "tfa_host",
        "ARLO_TFA_USER": "tfa_username",
        "ARLO_TFA_USERNAME": "tfa_username",
        "ARLO_TFA_PASSWORD": "tfa_password",
        "ARLO_TFA_NICKNAME": "tfa_nickname",
        "ARLO_TFA_TIMEOUT": "tfa_timeout",
        "ARLO_TFA_TOTAL_TIMEOUT": "tfa_total_timeout",
        "ARLO_TFA_RETRIES": "tfa_retries",
        "ARLO_TFA_DELAY": "tfa_delay",
        "ARLO_TFA_CIPHER_LIST": "cipher_list",
    }
    int_env_names = {
        "ARLO_TFA_TIMEOUT",
        "ARLO_TFA_TOTAL_TIMEOUT",
        "ARLO_TFA_RETRIES",
        "ARLO_TFA_DELAY",
    }

    for env_name, kwarg_name in optional_env.items():
        value = os.getenv(env_name)
        if value:
            kwargs[kwarg_name] = int(value) if env_name in int_env_names else value

    return kwargs


def _clear_session_dir(storage_dir: Path) -> None:
    for path in storage_dir.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _should_retry_with_fresh_session(arlo: pyaarlo.PyArlo, storage_dir: Path) -> bool:
    if arlo.is_connected:
        return False

    error = (arlo.last_error or "").lower()
    if not any(
        marker in error
        for marker in ("login failed", "token validation failed", "session start failed")
    ):
        return False

    return any(storage_dir.iterdir())


def connect_arlo(*, library_days: int, storage_dir: str | Path) -> pyaarlo.PyArlo:
    storage_dir = Path(storage_dir)
    kwargs = _build_kwargs(library_days=library_days, storage_dir=storage_dir)
    arlo = pyaarlo.PyArlo(**kwargs)

    if _should_retry_with_fresh_session(arlo, storage_dir):
        _clear_session_dir(storage_dir)
        arlo = pyaarlo.PyArlo(**kwargs)

    if not arlo.is_connected:
        raise RuntimeError(arlo.last_error or "Unable to connect to Arlo")

    return arlo
