"""Endpoint de salud (sin /api prefix para healthcheck del Dockerfile)."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})
