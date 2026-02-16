from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid
import json
from datetime import datetime

from database import get_db, Session as SessionModel, Message, SessionScore
from models import (
    SessionCreate,
    SessionResponse,
    SessionListResponse,
    MessageResponse,
    SessionScoreResponse,
    QuestionScore,
    SessionAnalytics,
)
from auth import get_current_user_id

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _session_to_response(s: SessionModel) -> SessionResponse:
    return SessionResponse(
        id=s.id,
        user_id=s.user_id,
        name=s.name,
        job_description=s.job_description,
        company_name=s.company_name or "",
        role_title=s.role_title or "",
        status=s.status,
        created_at=str(s.created_at) if s.created_at else "",
        completed_at=str(s.completed_at) if s.completed_at else None,
    )


@router.post("/", response_model=SessionResponse)
async def create_session(
    data: SessionCreate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    session_id = str(uuid.uuid4())
    now = datetime.utcnow()

    session = SessionModel(
        id=session_id,
        user_id=user_id,
        name=data.name,
        job_description=data.job_description,
        company_name=data.company_name or "",
        role_title=data.role_title or "",
        status="pending",
        created_at=now,
    )
    db.add(session)
    await db.commit()

    return SessionResponse(
        id=session_id,
        user_id=user_id,
        name=data.name,
        job_description=data.job_description,
        company_name=data.company_name or "",
        role_title=data.role_title or "",
        status="pending",
        created_at=now.isoformat(),
    )


@router.get("/", response_model=SessionListResponse)
async def list_sessions(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.user_id == user_id)
        .order_by(SessionModel.created_at.desc())
    )
    rows = result.scalars().all()
    return SessionListResponse(sessions=[_session_to_response(s) for s in rows])


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SessionModel).where(
            SessionModel.id == session_id, SessionModel.user_id == user_id
        )
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_response(s)


@router.post("/{session_id}/complete")
async def complete_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Mark a session as completed and trigger scoring."""
    result = await db.execute(
        select(SessionModel).where(
            SessionModel.id == session_id, SessionModel.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    now = datetime.utcnow()
    session.status = "completed"
    session.completed_at = now
    await db.commit()

    # Generate scores from messages
    await _generate_session_scores(db, session_id)

    return {"message": "Session completed", "completed_at": now.isoformat()}


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    # Verify session ownership
    result = await db.execute(
        select(SessionModel.id).where(
            SessionModel.id == session_id, SessionModel.user_id == user_id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Session not found")

    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.timestamp.asc())
    )
    rows = result.scalars().all()
    return [
        MessageResponse(
            id=m.id,
            session_id=m.session_id,
            role=m.role,
            content=m.content,
            timestamp=str(m.timestamp) if m.timestamp else "",
        )
        for m in rows
    ]


@router.get("/{session_id}/analytics", response_model=SessionAnalytics)
async def get_session_analytics(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    # Get session
    result = await db.execute(
        select(SessionModel).where(
            SessionModel.id == session_id, SessionModel.user_id == user_id
        )
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _session_to_response(s)

    # Get messages
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.timestamp.asc())
    )
    msg_rows = result.scalars().all()
    messages = [
        MessageResponse(
            id=m.id,
            session_id=m.session_id,
            role=m.role,
            content=m.content,
            timestamp=str(m.timestamp) if m.timestamp else "",
        )
        for m in msg_rows
    ]

    # Count questions (ASSISTANT messages that end with ?)
    total_questions = sum(
        1 for m in messages if m.role == "ASSISTANT" and m.content.strip().endswith("?")
    )

    # Calculate duration
    duration = None
    if s.completed_at and s.created_at:
        try:
            duration = (s.completed_at - s.created_at).total_seconds()
        except Exception:
            pass

    # Get score
    score = None
    result = await db.execute(
        select(SessionScore).where(SessionScore.session_id == session_id)
    )
    score_row = result.scalar_one_or_none()
    if score_row:
        score = SessionScoreResponse(
            id=score_row.id,
            session_id=score_row.session_id,
            overall_score=score_row.overall_score,
            communication_score=score_row.communication_score,
            technical_score=score_row.technical_score,
            problem_solving_score=score_row.problem_solving_score,
            confidence_score=score_row.confidence_score,
            relevance_score=score_row.relevance_score,
            strengths=json.loads(score_row.strengths or "[]"),
            improvements=json.loads(score_row.improvements or "[]"),
            detailed_feedback=score_row.detailed_feedback or "",
            question_scores=[
                QuestionScore(**q) for q in json.loads(score_row.question_scores or "[]")
            ],
            created_at=str(score_row.created_at) if score_row.created_at else "",
        )

    return SessionAnalytics(
        session=session,
        score=score,
        messages=messages,
        total_questions=total_questions,
        total_duration_seconds=duration,
    )


async def _generate_session_scores(db: AsyncSession, session_id: str):
    """Generate scores from interview messages using simple heuristic analysis.
    In production, this would call an LLM for proper evaluation."""
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.timestamp.asc())
    )
    rows = result.scalars().all()
    messages_data = [(m.role, m.content) for m in rows]

    if not messages_data:
        return

    # Simple heuristics for scoring
    user_messages = [m[1] for m in messages_data if m[0] == "USER"]
    assistant_messages = [m[1] for m in messages_data if m[0] == "ASSISTANT"]

    # Heuristic scoring
    total_user_words = sum(len(m.split()) for m in user_messages)
    avg_response_length = total_user_words / max(len(user_messages), 1)

    # Score based on response length (longer = more detailed = better, up to a point)
    communication = min(10, max(3, avg_response_length / 5))
    # Technical: based on keyword density
    technical_keywords = ["algorithm", "system", "design", "implement", "optimize", "database", "api", "architecture", "scale", "performance", "complexity", "data structure"]
    tech_count = sum(1 for m in user_messages for k in technical_keywords if k in m.lower())
    technical = min(10, max(3, 3 + tech_count * 0.8))
    # Problem solving: based on structured answers
    structure_keywords = ["first", "then", "because", "approach", "solution", "consider", "trade-off", "alternatively"]
    struct_count = sum(1 for m in user_messages for k in structure_keywords if k in m.lower())
    problem_solving = min(10, max(3, 3 + struct_count * 0.7))
    # Confidence: based on hedging language (fewer hedges = more confident)
    hedge_words = ["maybe", "i think", "probably", "not sure", "i guess", "kind of", "sort of"]
    hedge_count = sum(1 for m in user_messages for k in hedge_words if k in m.lower())
    confidence = min(10, max(3, 8 - hedge_count * 0.5))
    # Relevance: based on number of responses vs questions
    questions = [m for m in assistant_messages if m.strip().endswith("?")]
    relevance = min(10, max(3, 5 + min(len(user_messages), len(questions)) * 0.5))

    overall = round((communication + technical + problem_solving + confidence + relevance) / 5, 1)

    # Generate strengths and improvements
    strengths = []
    improvements = []

    if avg_response_length > 20:
        strengths.append("Provides detailed and thorough responses")
    else:
        improvements.append("Try to elaborate more on your answers with specific examples")

    if tech_count > 2:
        strengths.append("Demonstrates strong technical vocabulary")
    else:
        improvements.append("Incorporate more technical terminology relevant to the role")

    if struct_count > 2:
        strengths.append("Shows structured problem-solving approach")
    else:
        improvements.append("Structure your answers with a clear beginning, middle, and end")

    if hedge_count < 2:
        strengths.append("Projects confidence in responses")
    else:
        improvements.append("Reduce hedging language to project more confidence")

    if len(user_messages) >= len(questions) * 0.8:
        strengths.append("Addresses most questions raised by the interviewer")
    else:
        improvements.append("Make sure to address each question asked by the interviewer")

    # Generate question-level scores
    question_scores = []
    for i, (role, content) in enumerate(messages_data):
        if role == "ASSISTANT" and content.strip().endswith("?"):
            # Find the next user response
            user_response = ""
            for j in range(i + 1, len(messages_data)):
                if messages_data[j][0] == "USER":
                    user_response = messages_data[j][1]
                    break
            if user_response:
                words = len(user_response.split())
                q_score = min(10, max(3, words / 4))
                question_scores.append({
                    "question": content,
                    "answer_summary": user_response[:200],
                    "score": round(q_score, 1),
                    "feedback": "Good response" if q_score >= 6 else "Could be more detailed",
                })

    # Upsert: delete existing score if any, then insert new one
    existing = await db.execute(
        select(SessionScore).where(SessionScore.session_id == session_id)
    )
    old_score = existing.scalar_one_or_none()
    if old_score:
        await db.delete(old_score)

    score_id = str(uuid.uuid4())
    now = datetime.utcnow()

    new_score = SessionScore(
        id=score_id,
        session_id=session_id,
        overall_score=overall,
        communication_score=round(communication, 1),
        technical_score=round(technical, 1),
        problem_solving_score=round(problem_solving, 1),
        confidence_score=round(confidence, 1),
        relevance_score=round(relevance, 1),
        strengths=json.dumps(strengths),
        improvements=json.dumps(improvements),
        detailed_feedback=(
            f"Interview session completed with {len(user_messages)} responses to {len(questions)} questions. "
            f"Average response length: {int(avg_response_length)} words."
        ),
        question_scores=json.dumps(question_scores),
        created_at=now,
    )
    db.add(new_score)
    await db.commit()
