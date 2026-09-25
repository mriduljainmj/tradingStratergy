"""Stable local secrets, with explicit production configuration."""
import os
import secrets
from pathlib import Path


def app_secret():
    value = os.getenv('JWT_SECRET_KEY', '')
    if value:
        if os.getenv('APP_ENV') == 'production' and len(value.encode()) < 32:
            raise RuntimeError('JWT_SECRET_KEY must contain at least 32 bytes in production.')
        return value
    if os.getenv('APP_ENV') == 'production':
        raise RuntimeError('JWT_SECRET_KEY is required in production.')
    path = Path(os.getenv('APP_SECRET_FILE', str(Path(__file__).resolve().parents[1] / '.axiom_dev_secret')))
    try:
        with path.open('x') as stream:
            path.chmod(0o600)
            stream.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    return path.read_text().strip()
