from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func
import uuid
from datetime import datetime, timedelta
import secrets

from database import get_db, Recruiter, Job, InterviewInvite, SessionScore, Session
from models import (
    RecruiterRegister, RecruiterLogin, RecruiterResponse, RecruiterTokenResponse,
    JobCreate, JobResponse, InviteCreate, InviteResponse, JobWithInvitesResponse,
    BulkInviteCreate, BulkInviteResponse, InviteScoreSummary
)
from auth import hash_password, verify_password, create_access_token, get_current_recruiter_id
from email_service import send_invite_email

router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])


# ─── Auth Routes ────────────────────────────────────────────

@router.post("/signup", response_model=RecruiterTokenResponse)
async def register_recruiter(req: RecruiterRegister, db: AsyncSession = Depends(get_db)):
    # Check if email exists
    result = await db.execute(select(Recruiter).where(Recruiter.email == req.email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create recruiter
    recruiter_id = f"rec_{uuid.uuid4().hex[:12]}"
    hashed_pw = hash_password(req.password)
    new_recruiter = Recruiter(
        id=recruiter_id,
        email=req.email,
        name=req.name,
        company_name=req.company_name,
        password_hash=hashed_pw
    )

    db.add(new_recruiter)
    await db.commit()
    await db.refresh(new_recruiter)

    # Generate token
    token = create_access_token(new_recruiter.id, role="recruiter")

    return RecruiterTokenResponse(
        access_token=token,
        recruiter=RecruiterResponse(
            id=new_recruiter.id,
            email=new_recruiter.email,
            name=new_recruiter.name,
            company_name=new_recruiter.company_name,
            created_at=new_recruiter.created_at.isoformat()
        )
    )


@router.post("/login", response_model=RecruiterTokenResponse)
async def login_recruiter(req: RecruiterLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Recruiter).where(Recruiter.email == req.email))
    recruiter = result.scalars().first()

    if not recruiter or not verify_password(req.password, recruiter.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(recruiter.id, role="recruiter")

    return RecruiterTokenResponse(
        access_token=token,
        recruiter=RecruiterResponse(
            id=recruiter.id,
            email=recruiter.email,
            name=recruiter.name,
            company_name=recruiter.company_name,
            created_at=recruiter.created_at.isoformat()
        )
    )


@router.get("/me", response_model=RecruiterResponse)
async def get_me(current_id: str = Depends(get_current_recruiter_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Recruiter).where(Recruiter.id == current_id))
    recruiter = result.scalars().first()
    if not recruiter:
        raise HTTPException(status_code=404, detail="Recruiter not found")
        
    return RecruiterResponse(
        id=recruiter.id,
        email=recruiter.email,
        name=recruiter.name,
        company_name=recruiter.company_name,
        created_at=recruiter.created_at.isoformat()
    )


# ─── Job Routes ─────────────────────────────────────────────

@router.post("/jobs", response_model=JobResponse)
async def create_job(req: JobCreate, current_id: str = Depends(get_current_recruiter_id), db: AsyncSession = Depends(get_db)):
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    new_job = Job(
        id=job_id,
        recruiter_id=current_id,
        title=req.title,
        description=req.description,
        status="active"
    )
    
    db.add(new_job)
    await db.commit()
    await db.refresh(new_job)
    
    return JobResponse(
        id=new_job.id,
        recruiter_id=new_job.recruiter_id,
        title=new_job.title,
        description=new_job.description,
        status=new_job.status,
        created_at=new_job.created_at.isoformat()
    )

@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(current_id: str = Depends(get_current_recruiter_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).where(Job.recruiter_id == current_id).order_by(Job.created_at.desc()))
    jobs = result.scalars().all()
    
    return [
        JobResponse(
            id=job.id,
            recruiter_id=job.recruiter_id,
            title=job.title,
            description=job.description,
            status=job.status,
            created_at=job.created_at.isoformat()
        ) for job in jobs
    ]

@router.get("/jobs/{job_id}", response_model=JobWithInvitesResponse)
async def get_job_details(job_id: str, current_id: str = Depends(get_current_recruiter_id), db: AsyncSession = Depends(get_db)):
    # Validate job belongs to recruiter and fetch with invites + session scores
    result = await db.execute(
        select(Job)
        .options(
            selectinload(Job.invites)
            .selectinload(InterviewInvite.session)
            .selectinload(Session.score)
        )
        .where(Job.id == job_id, Job.recruiter_id == current_id)
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    invites_resp = []
    stats = {"pending": 0, "completed": 0, "total": len(job.invites)}
    
    for invite in job.invites:
        stats[invite.status] = stats.get(invite.status, 0) + 1
        
        # Build score summary if the interview is completed and has scores
        score_summary = None
        if invite.session_id and invite.session and hasattr(invite.session, 'score') and invite.session.score:
            sc = invite.session.score
            import json
            try:
                strengths = json.loads(sc.strengths) if isinstance(sc.strengths, str) else sc.strengths
            except (json.JSONDecodeError, TypeError):
                strengths = []
            try:
                improvements = json.loads(sc.improvements) if isinstance(sc.improvements, str) else sc.improvements
            except (json.JSONDecodeError, TypeError):
                improvements = []
            
            score_summary = InviteScoreSummary(
                overall_score=sc.overall_score,
                communication_score=sc.communication_score,
                technical_score=sc.technical_score,
                problem_solving_score=sc.problem_solving_score,
                confidence_score=sc.confidence_score,
                relevance_score=sc.relevance_score,
                strengths=strengths,
                improvements=improvements,
            )
        
        invites_resp.append(
            InviteResponse(
                id=invite.id,
                job_id=invite.job_id,
                candidate_email=invite.candidate_email,
                token=invite.token,
                status=invite.status,
                session_id=invite.session_id,
                score_summary=score_summary,
                expires_at=invite.expires_at.isoformat(),
                created_at=invite.created_at.isoformat()
            )
        )
        
    return JobWithInvitesResponse(
        job=JobResponse(
            id=job.id,
            recruiter_id=job.recruiter_id,
            title=job.title,
            description=job.description,
            status=job.status,
            created_at=job.created_at.isoformat()
        ),
        invites=invites_resp,
        stats=stats
    )


# ─── Invite Routes ──────────────────────────────────────────

@router.post("/jobs/{job_id}/invite", response_model=InviteResponse)
async def invite_candidate(job_id: str, req: InviteCreate, current_id: str = Depends(get_current_recruiter_id), db: AsyncSession = Depends(get_db)):
    # Validate job ownership and get recruiter for company name
    result = await db.execute(select(Job).where(Job.id == job_id, Job.recruiter_id == current_id))
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    recruiter_result = await db.execute(select(Recruiter).where(Recruiter.id == current_id))
    recruiter = recruiter_result.scalars().first()
    company_name = recruiter.company_name if recruiter else "Our Company"
        
    invite_id = f"inv_{uuid.uuid4().hex[:12]}"
    token = secrets.token_urlsafe(32) # Secure random token for URL
    expires_at = datetime.utcnow() + timedelta(days=req.expires_in_days)
    
    new_invite = InterviewInvite(
        id=invite_id,
        job_id=job_id,
        candidate_email=req.candidate_email,
        token=token,
        status="pending",
        expires_at=expires_at
    )
    
    db.add(new_invite)
    await db.commit()
    await db.refresh(new_invite)
    
    # Send invite email (non-blocking, doesn't fail the request)
    await send_invite_email(
        candidate_email=req.candidate_email,
        job_title=job.title,
        company_name=company_name,
        invite_token=token,
        expires_at=expires_at,
    )
    
    return InviteResponse(
        id=new_invite.id,
        job_id=new_invite.job_id,
        candidate_email=new_invite.candidate_email,
        token=new_invite.token,
        status=new_invite.status,
        session_id=new_invite.session_id,
        expires_at=new_invite.expires_at.isoformat(),
        created_at=new_invite.created_at.isoformat()
    )


@router.post("/jobs/{job_id}/invite/bulk", response_model=BulkInviteResponse)
async def bulk_invite_candidates(job_id: str, req: BulkInviteCreate, current_id: str = Depends(get_current_recruiter_id), db: AsyncSession = Depends(get_db)):
    # Validate job ownership and get recruiter for company name
    result = await db.execute(select(Job).where(Job.id == job_id, Job.recruiter_id == current_id))
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    recruiter_result = await db.execute(select(Recruiter).where(Recruiter.id == current_id))
    recruiter = recruiter_result.scalars().first()
    company_name = recruiter.company_name if recruiter else "Our Company"

    invited = []
    errors = []
    expires_at = datetime.utcnow() + timedelta(days=req.expires_in_days)

    for email_raw in req.candidate_emails:
        email = email_raw.strip().lower()
        if not email:
            continue

        # Basic email validation
        if "@" not in email or "." not in email.split("@")[-1]:
            errors.append({"email": email, "reason": "Invalid email format"})
            continue

        # Check for duplicate invite on this job
        existing = await db.execute(
            select(InterviewInvite).where(
                InterviewInvite.job_id == job_id,
                InterviewInvite.candidate_email == email
            )
        )
        if existing.scalars().first():
            errors.append({"email": email, "reason": "Already invited"})
            continue

        invite_id = f"inv_{uuid.uuid4().hex[:12]}"
        token = secrets.token_urlsafe(32)

        new_invite = InterviewInvite(
            id=invite_id,
            job_id=job_id,
            candidate_email=email,
            token=token,
            status="pending",
            expires_at=expires_at
        )
        db.add(new_invite)
        await db.flush()
        await db.refresh(new_invite)

        invited.append(
            InviteResponse(
                id=new_invite.id,
                job_id=new_invite.job_id,
                candidate_email=new_invite.candidate_email,
                token=new_invite.token,
                status=new_invite.status,
                session_id=new_invite.session_id,
                expires_at=new_invite.expires_at.isoformat(),
                created_at=new_invite.created_at.isoformat()
            )
        )

        # Send invite email (non-blocking, doesn't fail the request)
        await send_invite_email(
            candidate_email=email,
            job_title=job.title,
            company_name=company_name,
            invite_token=token,
            expires_at=expires_at,
        )

    await db.commit()

    return BulkInviteResponse(invited=invited, errors=errors)
