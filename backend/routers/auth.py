from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.models.user import User
from backend.auth import verify_password, hash_password, create_access_token, require_admin, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: dict


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None
    can_view_receivables: bool = False
    can_view_payables: bool = False
    can_view_sales: bool = False
    can_view_purchases: bool = False
    is_admin: bool = False


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None
    can_view_receivables: Optional[bool] = None
    can_view_payables: Optional[bool] = None
    can_view_sales: Optional[bool] = None
    can_view_purchases: Optional[bool] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "is_active": u.is_active,
        "is_admin": u.is_admin,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "can_view_receivables": u.can_view_receivables,
        "can_view_payables": u.can_view_payables,
        "can_view_sales": u.can_view_sales,
        "can_view_purchases": u.can_view_purchases,
    }


@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Neteisingas vartotojo vardas arba slaptažodis",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Paskyra užblokuota")

    token = create_access_token({"sub": user.username})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "is_admin": user.is_admin,
            "modules": {
                "receivables": user.can_view_receivables or user.is_admin,
                "payables": user.can_view_payables or user.is_admin,
                "sales": user.can_view_sales or user.is_admin,
                "purchases": user.can_view_purchases or user.is_admin,
            }
        }
    }


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "is_admin": current_user.is_admin,
        "modules": {
            "receivables": current_user.can_view_receivables or current_user.is_admin,
            "payables": current_user.can_view_payables or current_user.is_admin,
            "sales": current_user.can_view_sales or current_user.is_admin,
            "purchases": current_user.can_view_purchases or current_user.is_admin,
        }
    }


@router.post("/me/password")
def change_own_password(
    data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(data.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Dabartinis slaptažodis neteisingas")
    if len(data.new_password) < 4:
        raise HTTPException(status_code=400, detail="Slaptažodis per trumpas (min. 4 simboliai)")
    current_user.password_hash = hash_password(data.new_password)
    db.commit()
    return {"ok": True}


@router.get("/users", dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id).all()
    return [_user_dict(u) for u in users]


@router.post("/users", dependencies=[Depends(require_admin)])
def create_user(data: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Vartotojas jau egzistuoja")
    if len(data.password) < 4:
        raise HTTPException(status_code=400, detail="Slaptažodis per trumpas (min. 4 simboliai)")
    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        can_view_receivables=data.can_view_receivables,
        can_view_payables=data.can_view_payables,
        can_view_sales=data.can_view_sales,
        can_view_purchases=data.can_view_purchases,
        is_admin=data.is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_dict(user)


@router.patch("/users/{user_id}", dependencies=[Depends(require_admin)])
def update_user(user_id: int, data: UserUpdate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Vartotojas nerastas")
    if data.password is not None:
        if len(data.password) < 4:
            raise HTTPException(status_code=400, detail="Slaptažodis per trumpas (min. 4 simboliai)")
        user.password_hash = hash_password(data.password)
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.can_view_receivables is not None:
        user.can_view_receivables = data.can_view_receivables
    if data.can_view_payables is not None:
        user.can_view_payables = data.can_view_payables
    if data.can_view_sales is not None:
        user.can_view_sales = data.can_view_sales
    if data.can_view_purchases is not None:
        user.can_view_purchases = data.can_view_purchases
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.is_admin is not None:
        user.is_admin = data.is_admin
    db.commit()
    return _user_dict(user)


@router.delete("/users/{user_id}", dependencies=[Depends(require_admin)])
def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Negalima ištrinti savo paskyros")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Vartotojas nerastas")
    db.delete(user)
    db.commit()
    return {"ok": True}
