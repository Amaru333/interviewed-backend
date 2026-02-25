from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

from database import get_db, User
from models import UserRegister, UserLogin, UserResponse, TokenResponse
from auth import hash_password, verify_password, create_access_token, get_current_user_id

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
async def register(data: UserRegister, db: AsyncSession = Depends(get_db)):
    # Check if email exists
    result = await db.execute(select(User).where(User.email == data.email))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = str(uuid.uuid4())
    pw_hash = hash_password(data.password)

    user = User(
        id=user_id,
        email=data.email,
        name=data.name,
        password_hash=pw_hash,
        resume_text="",
        resume_filename="",
    )
    db.add(user)
    await db.commit()

    token = create_access_token(user_id)

    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user_id,
            email=data.email,
            name=data.name,
            resume_text="",
            resume_filename="",
            is_onboarded=False,
            created_at="",
        ),
    )


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(user.id)

    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            resume_text=user.resume_text or "",
            resume_filename=user.resume_filename or "",
            is_onboarded=user.is_onboarded or False,
            created_at=str(user.created_at) if user.created_at else "",
        ),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user_id: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        resume_text=user.resume_text or "",
        resume_filename=user.resume_filename or "",
        is_onboarded=user.is_onboarded or False,
        created_at=str(user.created_at) if user.created_at else "",
    )


@router.post("/onboarded")
async def mark_onboarded(user_id: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_onboarded = True
    await db.commit()
    return {"message": "Onboarding complete"}


@router.post("/resume")
async def upload_resume(
    resume_text: str,
    filename: str = "resume.txt",
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.resume_text = resume_text
    user.resume_filename = filename
    await db.commit()
    return {"message": "Resume uploaded successfully"}
