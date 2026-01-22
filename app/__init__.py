"""
Flask application factory.
"""

from flask import Flask

from .config import get_settings
from .extensions import close_db_session, init_db
from .logging_config import setup_logging


def create_app() -> Flask:
    setup_logging()
    settings = get_settings()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.secret_key
    app.teardown_appcontext(close_db_session)

    # ✅ Health check for Docker/K8s/LB (do NOT version this)
    @app.get("/health")
    def healthcheck():
        return {"status": "ok"}, 200

    # （可选）如果你想同时支持 /v1/health，也可以打开下面这段
    @app.get("/v1/health")
    def healthcheck_v1():
        return {"status": "ok"}, 200

    from .api import api, health
    app.register_blueprint(api)
    app.register_blueprint(health)

    @app.route("/")
    def root():
        return {"name": "Sentiment Intelligence Platform", "version": "1.0.0"}

    return app


def init_application() -> Flask:
    app = create_app()
    with app.app_context():
        init_db()
    return app


__all__ = ["create_app", "init_application"]
