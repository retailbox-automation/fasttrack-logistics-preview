"""Shipment attachments — upload documents / photos / files onto a shipment.

Stored in Postgres (MVP) with a per-file size cap. Linked to a shipment by its
public_id (the frontend LOADING_LISTS id) so attachments survive shipment bulk
re-saves. Migrate to a volume / object store if attachment volume grows.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth, require_roles
from app.audit import log_audit
from app.database import get_db
from app.events import broadcast
from app.models import Attachment

router = APIRouter(tags=["attachments"])

MAX_SIZE = 8 * 1024 * 1024  # 8 MB per file


class AttachmentOut(BaseModel):
    id: int
    shipment_public_id: str
    filename: str
    content_type: str
    size: int
    kind: str
    uploaded_by: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}


def _kind_for(content_type: str) -> str:
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return "photo"
    if ct == "application/pdf" or ct.startswith("text/") or "word" in ct or "excel" in ct or "spreadsheet" in ct:
        return "document"
    return "file"


@router.get("/api/shipments/{public_id}/attachments", response_model=list[AttachmentOut],
            dependencies=[Depends(require_auth)])
def list_attachments(public_id: str, db: Session = Depends(get_db)):
    return (db.query(Attachment)
            .filter(Attachment.shipment_public_id == public_id)
            .order_by(Attachment.created_at.desc()).all())


@router.post("/api/shipments/{public_id}/attachments", response_model=AttachmentOut)
async def upload_attachment(public_id: str, request: Request, file: UploadFile = File(...),
                            db: Session = Depends(get_db),
                            claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_SIZE // (1024 * 1024)} MB)")
    att = Attachment(
        shipment_public_id=public_id,
        filename=(file.filename or "upload")[:255],
        content_type=(file.content_type or "application/octet-stream")[:128],
        size=len(data),
        kind=_kind_for(file.content_type or ""),
        data=data,
        uploaded_by=claims.get("name") or claims.get("email"),
    )
    db.add(att); db.commit(); db.refresh(att)
    log_audit(db, claims, "upload", "attachment", entity_id=str(att.id),
              summary=f"Attachment {att.filename} on {public_id}",
              ip=request.client.host if request.client else None)
    broadcast("attachments.changed", {"action": "upload", "shipment": public_id, "by_name": claims.get("name")})
    return att


@router.get("/api/attachments/{att_id}/download", dependencies=[Depends(require_auth)])
def download_attachment(att_id: int, db: Session = Depends(get_db)):
    att = db.get(Attachment, att_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return Response(content=att.data, media_type=att.content_type,
                    headers={"Content-Disposition": f'inline; filename="{att.filename}"'})


@router.delete("/api/attachments/{att_id}", status_code=204)
def delete_attachment(att_id: int, request: Request, db: Session = Depends(get_db),
                      claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    att = db.get(Attachment, att_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    pid = att.shipment_public_id
    db.delete(att); db.commit()
    log_audit(db, claims, "delete", "attachment", entity_id=str(att_id),
              summary=f"Deleted attachment {att_id} from {pid}",
              ip=request.client.host if request.client else None)
    broadcast("attachments.changed", {"action": "delete", "shipment": pid, "by_name": claims.get("name")})
    return Response(status_code=204)
