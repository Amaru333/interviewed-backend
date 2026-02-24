from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db, User
from auth import get_current_user_id

router = APIRouter(prefix="/api/resume", tags=["resume"])


@router.post("/upload")
async def upload_resume(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Upload a resume file. Extracts text content for AI context."""
    content = await file.read()
    text_content = ""

    filename = file.filename or "resume"
    if filename.endswith(".pdf"):
        # Try extracting text from PDF using PyPDF2 if available
        try:
            import io
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
            text_content = "\n".join(pages).strip()
        except ImportError as e:
            # PyPDF2 not installed — fail loudly
            raise HTTPException(status_code=500, detail=f"PDF parsing library missing. Install PyPDF2 to extract text from {filename}.") from e
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not extract text from PDF: {filename}. Please provide a valid text-based PDF or use the text upload option.") from e
    else:
        text_content = content.decode("utf-8", errors="ignore")

    # PostgreSQL rejects null bytes in text columns — strip them
    text_content = text_content.replace("\x00", "")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.resume_text = text_content
    user.resume_filename = filename
    await db.commit()

    return {
        "message": "Resume uploaded successfully",
        "filename": filename,
        "text_length": len(text_content),
    }


@router.post("/text")
async def upload_resume_text(
    text: str = Form(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Upload resume as plain text."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.resume_text = text
    user.resume_filename = "resume_text_input"
    await db.commit()
    return {"message": "Resume text saved successfully"}
