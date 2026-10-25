"""Lifecycle helpers for regenerable transcript data."""
from sqlalchemy.orm import Session

from ..models import TranscriptSegment, Video


def clear_transcript_segments(db: Session, video: Video) -> bool:
    """Clear transcript rows only after an unprotected video is actually removed."""
    if video.protected:
        return False
    db.query(TranscriptSegment).filter(TranscriptSegment.video_id == video.id).delete(synchronize_session=False)
    return True
