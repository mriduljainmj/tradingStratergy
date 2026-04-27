import os
from flask import Flask

from core.state import BotState
from dashboard.routes import dashboard_bp, register_state, register_mode_switcher, register_backtester


def create_app(state: BotState, mode_switcher=None, backtester=None) -> Flask:
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    app = Flask(__name__, template_folder=template_dir)
    register_state(state)
    if mode_switcher:
        register_mode_switcher(mode_switcher)
    if backtester:
        register_backtester(backtester)
    app.register_blueprint(dashboard_bp)
    return app
