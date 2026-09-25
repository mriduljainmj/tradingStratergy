"""Serve the Angular production bundle from Flask (same-origin API)."""
from pathlib import Path
from flask import abort, send_from_directory

FRONTEND = Path(__file__).resolve().parents[1] / 'frontend' / 'dist' / 'frontend' / 'browser'


def page():
    if (FRONTEND / 'index.html').exists():
        return send_from_directory(FRONTEND, 'index.html')
    return (
        'Frontend build missing. Run "npm ci && npm run build" in frontend/ before starting the application.',
        503,
        {'Content-Type': 'text/plain; charset=utf-8'},
    )


def install_frontend(app):
    @app.get('/<path:path>')
    def frontend(path):
        if path.startswith(('api/', 'kite/', '.')):
            abort(404)
        candidate = (FRONTEND / path).resolve()
        if candidate.is_relative_to(FRONTEND.resolve()) and candidate.is_file():
            return send_from_directory(FRONTEND, path)
        if path in ('overview', 'portfolio', 'markets', 'strategies', 'backtests', 'results', 'charts', 'settings'):
            return page()
        abort(404)
