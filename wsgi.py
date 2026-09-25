"""Single-worker production entry point; engines own their background threads."""
from dashboard import create_app
from main import _restore_all_active_users

app = create_app()
_restore_all_active_users()
