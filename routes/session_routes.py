from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid
import json
import asyncio
import logging
import os
from datetime import datetime

import boto3

from database import get_db, Session as SessionModel, Message, SessionScore, User
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _session_to_response(s: SessionModel) -> SessionResponse:
    return SessionResponse(
        id=s.id,
        user_id=s.user_id,
        name=s.name,
        job_description=s.job_description,
        company_name=s.company_name or "",
        role_title=s.role_title or "",
        interviewer_name=s.interviewer_name or "",
        interviewer_voice=s.interviewer_voice or "",
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


@router.get("/progress")
async def get_progress(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return aggregated progress data across all completed sessions."""
    # Get all completed sessions with their scores
    result = await db.execute(
        select(SessionModel, SessionScore)
        .outerjoin(SessionScore, SessionModel.id == SessionScore.session_id)
        .where(SessionModel.user_id == user_id)
        .order_by(SessionModel.created_at.asc())
    )
    rows = result.all()

    total_sessions = len(rows)
    completed_sessions = sum(1 for s, _ in rows if s.status == "completed")
    scored_rows = [(s, sc) for s, sc in rows if sc is not None]

    # Score trend (chronological)
    score_trend = []
    for s, sc in scored_rows:
        score_trend.append({
            "session_name": s.name,
            "date": s.created_at.strftime("%Y-%m-%d") if s.created_at else "",
            "overall": round(sc.overall_score, 1),
            "communication": round(sc.communication_score, 1),
            "technical": round(sc.technical_score, 1),
            "problem_solving": round(sc.problem_solving_score, 1),
            "confidence": round(sc.confidence_score, 1),
            "relevance": round(sc.relevance_score, 1),
        })

    # Skill averages
    if scored_rows:
        n = len(scored_rows)
        skill_averages = {
            "communication": round(sum(sc.communication_score for _, sc in scored_rows) / n, 1),
            "technical": round(sum(sc.technical_score for _, sc in scored_rows) / n, 1),
            "problem_solving": round(sum(sc.problem_solving_score for _, sc in scored_rows) / n, 1),
            "confidence": round(sum(sc.confidence_score for _, sc in scored_rows) / n, 1),
            "relevance": round(sum(sc.relevance_score for _, sc in scored_rows) / n, 1),
        }
        average_score = round(sum(sc.overall_score for _, sc in scored_rows) / n, 1)
        best_score = round(max(sc.overall_score for _, sc in scored_rows), 1)

        # Top / weakest
        top_strength = max(skill_averages, key=skill_averages.get)
        weakest_skill = min(skill_averages, key=skill_averages.get)
    else:
        skill_averages = {"communication": 0, "technical": 0, "problem_solving": 0, "confidence": 0, "relevance": 0}
        average_score = 0
        best_score = 0
        top_strength = None
        weakest_skill = None

    # Total practice minutes
    total_minutes = 0
    for s, _ in rows:
        if s.completed_at and s.created_at:
            try:
                total_minutes += (s.completed_at - s.created_at).total_seconds() / 60
            except Exception:
                pass
    total_practice_minutes = round(total_minutes)

    # Current streak: consecutive days with at least one completed session (going backwards from today).
    # NOTE: completed_at is stored in UTC. date_type.today() also returns the server's UTC date.
    # Users in UTC-negative timezones may appear to lose their streak early if they practice late
    # in their local evening (which is already "tomorrow" in UTC). A future improvement would be
    # to accept the user's timezone offset from the frontend and adjust accordingly.
    from datetime import date as date_type
    completed_dates = set()
    for s, _ in rows:
        if s.status == "completed" and s.completed_at:
            completed_dates.add(s.completed_at.date())

    current_streak = 0
    check_date = date_type.today()
    while check_date in completed_dates:
        current_streak += 1
        check_date = check_date - __import__("datetime").timedelta(days=1)

    # Pretty names for skills
    skill_labels = {
        "communication": "Communication",
        "technical": "Technical",
        "problem_solving": "Problem Solving",
        "confidence": "Confidence",
        "relevance": "Relevance",
    }

    return {
        "total_sessions": total_sessions,
        "completed_sessions": completed_sessions,
        "average_score": average_score,
        "best_score": best_score,
        "total_practice_minutes": total_practice_minutes,
        "current_streak": current_streak,
        "score_trend": score_trend,
        "skill_averages": skill_averages,
        "top_strength": skill_labels.get(top_strength, top_strength) if top_strength else None,
        "weakest_skill": skill_labels.get(weakest_skill, weakest_skill) if weakest_skill else None,
    }


from sqlalchemy import or_, func

@router.get("/", response_model=SessionListResponse)
async def list_sessions(
    page: int = 1,
    limit: int = 10,
    search: str = None,
    status_filter: str = "all",
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    # 1. Compute Global Stats for the user
    global_result = await db.execute(
        select(SessionModel.status).where(SessionModel.user_id == user_id)
    )
    statuses = global_result.scalars().all()
    global_total = len(statuses)
    global_completed = sum(1 for s in statuses if s == "completed")
    global_active = sum(1 for s in statuses if s != "completed")  # 'pending', 'active', etc.

    # 2. Build Query
    query = select(SessionModel).where(SessionModel.user_id == user_id)

    if status_filter == "completed":
        query = query.where(SessionModel.status == "completed")
    elif status_filter == "active":
        query = query.where(SessionModel.status != "completed")

    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                SessionModel.name.ilike(search_term),
                SessionModel.role_title.ilike(search_term),
                SessionModel.company_name.ilike(search_term)
            )
        )

    # 3. Get total filtered count
    count_query = select(func.count()).select_from(query.subquery())
    total_matches = await db.scalar(count_query)

    # 4. Apply Pagination & Order
    query = query.order_by(SessionModel.created_at.desc())
    offset = (page - 1) * limit
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.scalars().all()

    pages = max(1, (total_matches + limit - 1) // limit)

    return SessionListResponse(
        sessions=[_session_to_response(s) for s in rows],
        total=total_matches,
        page=page,
        pages=pages,
        global_total=global_total,
        global_active=global_active,
        global_completed=global_completed
    )

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

    # Count questions: any ASSISTANT message that contains at least one "?"
    # (more robust than endswith — catches multi-sentence turns with a question inside)
    total_questions = sum(
        1 for m in messages if m.role == "ASSISTANT" and "?" in m.content
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
            strengths=[s for s in json.loads(score_row.strengths or "[]") if s],
            improvements=[i for i in json.loads(score_row.improvements or "[]") if i],
            detailed_feedback=score_row.detailed_feedback or "",
            question_scores=[
                QuestionScore(**q) for q in json.loads(score_row.question_scores or "[]") if q
            ],
            created_at=str(score_row.created_at) if score_row.created_at else "",
        )
        # Override with AI-counted questions if available (more accurate than heuristic)
        ai_question_count = len(json.loads(score_row.question_scores or "[]"))
        if ai_question_count > 0:
            total_questions = ai_question_count

    return SessionAnalytics(
        session=session,
        score=score,
        messages=messages,
        total_questions=total_questions,
        total_duration_seconds=duration,
    )


# ─── Tool config for structured output via constrained decoding ──────

SCORING_TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "submit_interview_evaluation",
                "description": "Submit the structured evaluation of a practice interview",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "overall_score": {
                                "type": "number",
                                "description": "Weighted average score from 1.0 to 10.0",
                            },
                            "communication_score": {
                                "type": "number",
                                "description": "Clarity, articulation, structure of responses, use of examples (1.0-10.0)",
                            },
                            "technical_score": {
                                "type": "number",
                                "description": "Depth of technical knowledge, correct terminology, relevance to the role (1.0-10.0)",
                            },
                            "problem_solving_score": {
                                "type": "number",
                                "description": "Structured thinking, analytical approach, trade-off consideration (1.0-10.0)",
                            },
                            "confidence_score": {
                                "type": "number",
                                "description": "Decisiveness, assertiveness, absence of excessive hedging (1.0-10.0)",
                            },
                            "relevance_score": {
                                "type": "number",
                                "description": "How well answers address questions and relate to job requirements (1.0-10.0)",
                            },
                            "strengths": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "description": "A specific strength observed in the interview",
                                },
                                "description": "3-5 specific strengths observed",
                            },
                            "improvements": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "description": "A specific, actionable area for improvement",
                                },
                                "description": "3-5 specific, actionable areas for improvement",
                            },
                            "detailed_feedback": {
                                "type": "string",
                                "description": "2-4 sentence overall summary of the candidate's performance",
                            },
                            "question_scores": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "question": {
                                            "type": "string",
                                            "description": "The interviewer's question text",
                                        },
                                        "answer_summary": {
                                            "type": "string",
                                            "description": "1-2 sentence summary of the candidate's answer",
                                        },
                                        "score": {
                                            "type": "number",
                                            "description": "Score from 1.0 to 10.0. Use the full range: strong answers should be 7-10, average answers 4-6, weak answers 1-3. Differentiate between questions based on quality.",
                                        },
                                        "feedback": {
                                            "type": "string",
                                            "description": "1-2 sentence specific feedback for this answer",
                                        },
                                    },
                                    "required": ["question", "answer_summary", "score", "feedback"],
                                },
                                "description": "Per-question evaluation, one entry per interviewer question",
                            },
                        },
                        "required": [
                            "overall_score",
                            "communication_score",
                            "technical_score",
                            "problem_solving_score",
                            "confidence_score",
                            "relevance_score",
                            "strengths",
                            "improvements",
                            "detailed_feedback",
                            "question_scores",
                        ],
                    }
                },
            }
        }
    ],
    "toolChoice": {"tool": {"name": "submit_interview_evaluation"}},
}


def _build_scoring_prompt(messages_data: list, job_description: str, role_title: str, resume_text: str = "") -> str:
    """Build the user prompt for interview evaluation."""
    transcript_lines = []
    for role, content in messages_data:
        speaker = "Interviewer" if role == "ASSISTANT" else "Candidate"
        transcript_lines.append(f"{speaker}: {content}")
    transcript = "\n".join(transcript_lines)

    resume_section = (
        f"\n--- CANDIDATE RESUME ---\n{resume_text}\n--- END RESUME ---\n"
        if resume_text
        else ""
    )

    return f"""Analyze the following practice interview transcript and evaluate the candidate's performance.

Role: {role_title or 'Not specified'}
Job Description: {job_description or 'Not specified'}
{resume_section}
--- TRANSCRIPT ---
{transcript}
--- END TRANSCRIPT ---

Evaluate the candidate on communication, technical knowledge, problem solving, confidence, and relevance to the job description and their own stated resume experience. Provide an overall score, specific strengths, areas for improvement, detailed feedback, and per-question scores.

IMPORTANT: Use the full 1-10 scoring range. Do NOT give the same score for every question. Differentiate based on quality — strong, specific answers with examples should score higher (7-10) than vague or incomplete answers (1-5). Be honest and critical. When a candidate's answer contradicts or under-represents their resume experience, call it out."""


def _call_nova_lite(prompt: str) -> dict:
    """Call Amazon Nova Lite via Bedrock Converse API with structured output.
    Uses toolConfig with constrained decoding for guaranteed schema compliance.
    Runs in a thread executor from the async caller."""
    region = os.getenv("AWS_REGION", "us-east-1")
    client = boto3.client("bedrock-runtime", region_name=region)

    response = client.converse(
        modelId="us.amazon.nova-2-lite-v1:0",
        system=[{"text": "You are an expert interview coach who provides detailed, constructive evaluations of practice interview performances."}],
        messages=[
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
        inferenceConfig={
            "maxTokens": 4096,
            "temperature": 0.5,
            "topP": 0.9,
        },
        toolConfig=SCORING_TOOL_CONFIG,
    )

    # With constrained decoding, the response comes as a tool use result — already parsed
    output_message = response["output"]["message"]
    for content_block in output_message["content"]:
        if "toolUse" in content_block:
            return content_block["toolUse"]["input"]

    # Shouldn't happen with toolChoice forcing the tool, but handle gracefully
    raise ValueError("No tool use result found in Nova response")


def _heuristic_fallback(messages_data: list) -> dict:
    """Heuristic scoring used when the AI scoring call fails.

    Scores use the full 1–10 range. Word count alone is NOT treated as quality;
    we additionally weight vocabulary richness and structural markers.
    """
    user_messages = [m[1] for m in messages_data if m[0] == "USER"]
    assistant_messages = [m[1] for m in messages_data if m[0] == "ASSISTANT"]

    if not user_messages:
        return {
            "overall_score": 1.0,
            "communication_score": 1.0,
            "technical_score": 1.0,
            "problem_solving_score": 1.0,
            "confidence_score": 1.0,
            "relevance_score": 1.0,
            "strengths": [],
            "improvements": ["No candidate responses were recorded."],
            "detailed_feedback": "No candidate responses were recorded in this session.",
            "question_scores": [],
        }

    total_user_words = sum(len(m.split()) for m in user_messages)
    avg_response_length = total_user_words / len(user_messages)

    # Communication: penalise very short AND very long (rambling) answers
    # Sweet spot is ~50–150 words; normalise to 1–10
    if avg_response_length < 5:
        communication = 1.0
    elif avg_response_length < 20:
        communication = 1.0 + (avg_response_length - 5) / 15 * 3  # 1–4
    elif avg_response_length < 150:
        communication = 4.0 + (avg_response_length - 20) / 130 * 5  # 4–9
    else:
        communication = max(5.0, 9.0 - (avg_response_length - 150) / 100)  # slight penalty for rambling

    technical_keywords = [
        "algorithm", "system", "design", "implement", "optimize", "database",
        "api", "architecture", "scale", "performance", "complexity",
        "data structure", "framework", "infrastructure", "pipeline", "latency",
    ]
    tech_count = sum(1 for m in user_messages for k in technical_keywords if k in m.lower())
    technical = min(10.0, max(1.0, 2.0 + tech_count * 0.7))

    structure_keywords = [
        "first", "second", "third", "then", "finally", "because", "approach",
        "solution", "consider", "trade-off", "alternatively", "in summary",
        "to summarise", "for example", "such as", "specifically",
    ]
    struct_count = sum(1 for m in user_messages for k in structure_keywords if k in m.lower())
    problem_solving = min(10.0, max(1.0, 2.0 + struct_count * 0.65))

    hedge_words = ["maybe", "i think", "probably", "not sure", "i guess", "kind of", "sort of", "i'm not sure"]
    hedge_count = sum(1 for m in user_messages for k in hedge_words if k in m.lower())
    confidence = min(10.0, max(1.0, 8.0 - hedge_count * 0.6))

    questions = [m for m in assistant_messages if "?" in m]
    answered_ratio = min(len(user_messages), len(questions)) / max(len(questions), 1)
    relevance = min(10.0, max(1.0, 3.0 + answered_ratio * 6.0))

    overall = round((communication + technical + problem_solving + confidence + relevance) / 5, 1)

    strengths = []
    improvements = []
    if avg_response_length >= 40:
        strengths.append("Provides sufficiently detailed responses")
    elif avg_response_length >= 15:
        improvements.append("Try to elaborate more — aim for at least 40–80 words per answer with specific examples")
    else:
        improvements.append("Answers are very brief; use the STAR method (Situation, Task, Action, Result) to structure fuller responses")

    if tech_count >= 4:
        strengths.append("Demonstrates strong technical vocabulary")
    elif tech_count >= 2:
        strengths.append("Shows some technical knowledge")
    else:
        improvements.append("Incorporate more technical terminology and specifics relevant to the role")

    if struct_count >= 4:
        strengths.append("Uses clear structure and logical flow in answers")
    elif struct_count >= 2:
        strengths.append("Shows some structural organisation in responses")
    else:
        improvements.append("Structure answers with clear signposting (e.g. 'First…', 'The approach I took was…')")

    if hedge_count == 0:
        strengths.append("Projects strong confidence — minimal hedging language")
    elif hedge_count <= 2:
        improvements.append("Reduce hedging phrases ('I think', 'maybe') to project more confidence")
    else:
        improvements.append("Excessive hedging detected — practice delivering answers with conviction")

    if answered_ratio >= 0.9:
        strengths.append("Addresses virtually all of the interviewer's questions")
    elif answered_ratio >= 0.7:
        improvements.append("A few questions appeared unanswered — make sure to address every question asked")
    else:
        improvements.append("Several questions went unanswered; listen carefully and ensure you respond to each one")

    question_scores = []
    for i, (role, content) in enumerate(messages_data):
        if role == "ASSISTANT" and "?" in content:
            user_response = ""
            for j in range(i + 1, len(messages_data)):
                if messages_data[j][0] == "USER":
                    user_response = messages_data[j][1]
                    break
            if user_response:
                words = len(user_response.split())
                # Score out of 10 based on response length with a reasonable curve
                if words < 5:
                    q_score = 1.0
                elif words < 20:
                    q_score = 1.0 + (words - 5) / 15 * 3
                elif words < 100:
                    q_score = 4.0 + (words - 20) / 80 * 4
                else:
                    q_score = min(10.0, 8.0 + (words - 100) / 100)
                question_scores.append({
                    "question": content,
                    "answer_summary": user_response[:200],
                    "score": round(q_score, 1),
                    "feedback": (
                        "Strong, detailed response." if q_score >= 7
                        else "Decent answer but could benefit from more depth and examples." if q_score >= 4
                        else "Answer was too brief — expand with specific examples and context."
                    ),
                })

    return {
        "overall_score": overall,
        "communication_score": round(communication, 1),
        "technical_score": round(technical, 1),
        "problem_solving_score": round(problem_solving, 1),
        "confidence_score": round(confidence, 1),
        "relevance_score": round(relevance, 1),
        "strengths": strengths,
        "improvements": improvements,
        "detailed_feedback": (
            f"Session completed with {len(user_messages)} candidate responses to "
            f"{len(questions)} interviewer questions. "
            f"Average response length: {int(avg_response_length)} words. "
            "(Note: this is an automated estimate — AI scoring was unavailable.)"
        ),
        "question_scores": question_scores,
    }


async def _generate_session_scores(db: AsyncSession, session_id: str):
    """Generate scores from interview messages using Amazon Nova Lite AI analysis.
    Falls back to heuristic scoring if the AI call fails."""
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.timestamp.asc())
    )
    rows = result.scalars().all()
    messages_data = [(m.role, m.content) for m in rows]

    if not messages_data:
        return

    # Get session context and user resume for the AI prompt
    session_result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session_row = session_result.scalar_one_or_none()
    job_description = session_row.job_description if session_row else ""
    role_title = session_row.role_title if session_row else ""

    resume_text = ""
    if session_row and session_row.user_id:
        user_result = await db.execute(
            select(User).where(User.id == session_row.user_id)
        )
        user_row = user_result.scalar_one_or_none()
        resume_text = (user_row.resume_text or "") if user_row else ""

    # Try AI scoring, fall back to heuristic
    scores = None
    try:
        prompt = _build_scoring_prompt(messages_data, job_description, role_title, resume_text)
        logger.info(f"Calling Amazon Nova Lite for interview scoring (session {session_id})")
        scores = await asyncio.to_thread(_call_nova_lite, prompt)
        logger.info(f"AI scoring completed for session {session_id}")
    except Exception as e:
        logger.warning(f"AI scoring failed for session {session_id}, falling back to heuristic: {e}")
        scores = _heuristic_fallback(messages_data)

    # Validate and clamp scores
    def clamp(val, lo=1.0, hi=10.0):
        try:
            return round(min(hi, max(lo, float(val))), 1)
        except (TypeError, ValueError):
            return 5.0

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
        overall_score=clamp(scores.get("overall_score", 5)),
        communication_score=clamp(scores.get("communication_score", 5)),
        technical_score=clamp(scores.get("technical_score", 5)),
        problem_solving_score=clamp(scores.get("problem_solving_score", 5)),
        confidence_score=clamp(scores.get("confidence_score", 5)),
        relevance_score=clamp(scores.get("relevance_score", 5)),
        strengths=json.dumps([s for s in scores.get("strengths", []) if s]),
        improvements=json.dumps([i for i in scores.get("improvements", []) if i]),
        detailed_feedback=scores.get("detailed_feedback", ""),
        question_scores=json.dumps(scores.get("question_scores", [])),
        created_at=now,
    )
    db.add(new_score)
    await db.commit()
