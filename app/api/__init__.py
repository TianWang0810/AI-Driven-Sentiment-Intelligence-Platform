"""
API package.

Contains Flask blueprints for API routes.
"""

from .routes import api, health

__all__ = ["api", "health"]