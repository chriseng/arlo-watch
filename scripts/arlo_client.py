"""Shared Arlo client configuration."""

import os
from pathlib import Path

import pyaarlo


def connect_arlo(*, library_days: int, storage_dir: str | Path) -> pyaarlo.PyArlo:
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

    return pyaarlo.PyArlo(**kwargs)
