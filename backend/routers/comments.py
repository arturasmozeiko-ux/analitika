from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.models.comment import Comment
from backend.auth import get_current_user, require_admin
from backend.models.user import User

router = APIRouter(prefix="/api/comments", tags=["comments"])


class CommentCreate(BaseModel):
    module: str
    entity_type: str
    entity_id: str
    text: str


class CommentUpdate(BaseModel):
    text: str


def _comment_dict(c: Comment) -> dict:
    return {
        "id": c.id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "user_id": c.user_id,
        "username": c.user.username if c.user else None,
        "full_name": c.user.full_name if c.user else None,
        "module": c.module,
        "entity_type": c.entity_type,
        "entity_id": c.entity_id,
        "text": c.text,
    }


@router.get("")
def list_comments(
    module: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Comment)
    if module:
        q = q.filter(Comment.module == module)
    if entity_type:
        q = q.filter(Comment.entity_type == entity_type)
    if entity_id:
        q = q.filter(Comment.entity_id == entity_id)
    comments = q.order_by(Comment.created_at.asc()).all()
    return [_comment_dict(c) for c in comments]


@router.post("")
def create_comment(
    data: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="Komentaras negali būti tuščias")
    c = Comment(
        user_id=current_user.id,
        module=data.module,
        entity_type=data.entity_type,
        entity_id=data.entity_id,
        text=data.text.strip(),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _comment_dict(c)


@router.patch("/{comment_id}")
def update_comment(
    comment_id: int,
    data: CommentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    c = db.query(Comment).filter(Comment.id == comment_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Komentaras nerastas")
    if c.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Negalite redaguoti šio komentaro")
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="Komentaras negali būti tuščias")
    c.text = data.text.strip()
    db.commit()
    return _comment_dict(c)


@router.delete("/{comment_id}")
def delete_comment(
    comment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    c = db.query(Comment).filter(Comment.id == comment_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Komentaras nerastas")
    if c.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Negalite trinti šio komentaro")
    db.delete(c)
    db.commit()
    return {"ok": True}
