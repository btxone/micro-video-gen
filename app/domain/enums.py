from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineStage(StrEnum):
    QUEUED = "queued"
    VALIDATE_INPUTS = "validate_inputs"
    ANALYZE_IMAGE = "analyze_image"
    ENHANCE_PRIMARY = "enhance_primary"
    VERIFY_INTEGRITY = "verify_integrity"
    ENHANCE_REFERENCES = "enhance_references"
    BUILD_PROMPT = "build_prompt"
    GENERATE_VIDEO = "generate_video"
    VIDEO_QC = "video_qc"
    PUBLISH = "publish"
