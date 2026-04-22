from datetime import datetime, timedelta
from typing import Optional
import os
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models.user import User

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))  # 8h

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Neteisingi prisijungimo duomenys",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_receivables(current_user: User = Depends(get_current_user)) -> User:
    if not (current_user.can_view_receivables or current_user.is_admin):
        raise HTTPException(status_code=403, detail="Neturite prieigos prie klientų skolų modulio")
    return current_user


def require_payables(current_user: User = Depends(get_current_user)) -> User:
    if not (current_user.can_view_payables or current_user.is_admin):
        raise HTTPException(status_code=403, detail="Neturite prieigos prie skolų tiekėjams modulio")
    return current_user


def require_sales(current_user: User = Depends(get_current_user)) -> User:
    if not (current_user.can_view_sales or current_user.is_admin):
        raise HTTPException(status_code=403, detail="Neturite prieigos prie pardavimų modulio")
    return current_user


def require_purchases(current_user: User = Depends(get_current_user)) -> User:
    if not (current_user.can_view_purchases or current_user.is_admin):
        raise HTTPException(status_code=403, detail="Neturite prieigos prie pirkimų modulio")
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Tik administratoriams")
    return current_user
