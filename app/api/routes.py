"""
API routes for the Sentiment Intelligence Platform.

Endpoints:
    POST /v1/jobs              - Create a new job
    GET  /v1/jobs/{id}         - Get job status and details
    GET  /v1/jobs/{id}/items   - Get items with pagination
    GET  /v1/jobs/{id}/alerts  - Get alerts for a job
    GET  /healthz              - Health check

All responses are JSON with consistent error format:
{
    "error": {
        "code": "ERROR_CODE",
        "message": "Human readable message"
    },
    "request_id": "abc123"
}
"""

from flask import Blueprint, g, jsonify, request
from pydantic import BaseModel, Field, field_validator
from typing import Optional

from ..extensions import get_db_context
from ..logging_config import get_logger, request_id_var, generate_request_id
from ..models import Job, JobStatus, Item, Alert

logger = get_logger(__name__)

# Create blueprint with /v1 prefix
api = Blueprint("api", __name__, url_prefix="/v1")


# =============================================================================
# Request/Response Models
# =============================================================================

class CreateJobRequest(BaseModel):
    """Request body for POST /v1/jobs."""
    
    source_type: str = Field(
        ..., pattern="^(youtube|webpage|rss)$",
        description="Source type: youtube, webpage, or rss"
    )
    source_url: str = Field(
        ..., min_length=10,
        description="URL to crawl"
    )
    options: Optional[dict] = Field(
        default=None,
        description="Options: {max_items, sort, ...}"
    )
    
    @field_validator("source_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Ensure URL has valid scheme."""
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v
    
    @field_validator("options")
    @classmethod
    def validate_options(cls, v: Optional[dict]) -> Optional[dict]:
        """Validate options if provided."""
        if v is None:
            return v
        
        # Validate max_items if present
        if "max_items" in v:
            max_items = v["max_items"]
            if not isinstance(max_items, int) or max_items < 1 or max_items > 1000:
                raise ValueError("max_items must be integer between 1 and 1000")
        
        # Validate sort if present
        if "sort" in v:
            sort = v["sort"]
            if sort not in ("newest", "oldest", "popular"):
                raise ValueError("sort must be one of: newest, oldest, popular")
        
        return v


# =============================================================================
# Error Handling
# =============================================================================

def error_response(code: str, message: str, status_code: int = 400):
    """Create standardized error response."""
    return jsonify({
        "error": {
            "code": code,
            "message": message,
        },
        "request_id": request_id_var.get(),
    }), status_code


# =============================================================================
# Request Hooks
# =============================================================================

@api.before_request
def before_request():
    """Set up request context."""
    # Generate and bind request ID
    req_id = generate_request_id()
    request_id_var.set(req_id)
    g.request_id = req_id


@api.after_request
def after_request(response):
    """Add request ID to response headers."""
    response.headers["X-Request-ID"] = g.get("request_id", "")
    return response


# =============================================================================
# Routes
# =============================================================================

@api.route("/jobs", methods=["POST"])
def create_job():
    """
    Create a new sentiment analysis job.
    
    Request body:
    {
        "source_type": "youtube|webpage|rss",
        "source_url": "https://...",
        "options": {"max_items": 100, "sort": "newest"}
    }
    
    Response:
    {
        "job_id": 1,
        "status": "queued"
    }
    """
    # Validate request
    try:
        data = CreateJobRequest(**request.json)
    except Exception as e:
        logger.warning("validation_error", error=str(e))
        return error_response("VALIDATION_ERROR", str(e), 400)
    
    # Create job
    with get_db_context() as session:
        job = Job(
            source_type=data.source_type,
            source_url=data.source_url,
            options=data.options,
            status=JobStatus.QUEUED,
            attempts=0,
            max_attempts=3,
            progress=0,
        )
        session.add(job)
        session.flush()  # Get the ID
        
        job_id = job.id
        status = job.status.value
    
    logger.info(
        "job_created",
        job_id=job_id,
        source_type=data.source_type,
    )
    
    return jsonify({
        "job_id": job_id,
        "status": status,
    }), 201


@api.route("/jobs/<int:job_id>", methods=["GET"])
def get_job(job_id: int):
    """
    Get job status and details.
    
    Response:
    {
        "id": 1,
        "source_type": "youtube",
        "source_url": "https://...",
        "status": "succeeded",
        "attempts": 1,
        "progress": 50,
        "error_code": null,
        "error_message": null,
        "created_at": "2024-01-15T10:30:00Z",
        ...
    }
    """
    with get_db_context() as session:
        job = session.query(Job).filter(Job.id == job_id).first()
        
        if not job:
            return error_response("NOT_FOUND", f"Job {job_id} not found", 404)
        
        return jsonify(job.to_dict())


@api.route("/jobs/<int:job_id>/items", methods=["GET"])
def get_job_items(job_id: int):
    """
    Get items for a job with pagination.
    
    Query parameters:
        - limit: Max items to return (default 50, max 100)
        - offset: Number of items to skip (default 0)
    
    Response:
    {
        "items": [...],
        "total": 100,
        "limit": 50,
        "offset": 0
    }
    """
    # Parse query parameters
    limit = min(int(request.args.get("limit", 50)), 100)
    offset = int(request.args.get("offset", 0))
    
    with get_db_context() as session:
        # Check job exists
        job = session.query(Job).filter(Job.id == job_id).first()
        if not job:
            return error_response("NOT_FOUND", f"Job {job_id} not found", 404)
        
        # Query items with pagination
        query = session.query(Item).filter(Item.job_id == job_id)
        total = query.count()
        items = query.order_by(Item.id).offset(offset).limit(limit).all()
        
        return jsonify({
            "items": [item.to_dict() for item in items],
            "total": total,
            "limit": limit,
            "offset": offset,
        })


@api.route("/jobs/<int:job_id>/alerts", methods=["GET"])
def get_job_alerts(job_id: int):
    """
    Get alerts for a job.
    
    Response:
    {
        "alerts": [
            {
                "id": 1,
                "type": "negative_spike",
                "payload": {...},
                "created_at": "..."
            }
        ]
    }
    """
    with get_db_context() as session:
        # Check job exists
        job = session.query(Job).filter(Job.id == job_id).first()
        if not job:
            return error_response("NOT_FOUND", f"Job {job_id} not found", 404)
        
        # Query alerts
        alerts = session.query(Alert).filter(Alert.job_id == job_id).all()
        
        return jsonify({
            "alerts": [alert.to_dict() for alert in alerts],
        })


# =============================================================================
# Health Check (outside /v1 prefix)
# =============================================================================

health = Blueprint("health", __name__)


@health.route("/healthz", methods=["GET"])
def healthz():
    """
    Health check endpoint.
    
    Response: {"status": "ok"}
    """
    return jsonify({"status": "ok"})


__all__ = ["api", "health"]