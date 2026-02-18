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

    # Current streak: consecutive days with at least one completed session (going backwards from today)
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


def _build_scoring_prompt(messages_data: list, job_description: str, role_title: str) -> str:
    """Build the user prompt for interview evaluation."""
    transcript_lines = []
    for role, content in messages_data:
        speaker = "Interviewer" if role == "ASSISTANT" else "Candidate"
        transcript_lines.append(f"{speaker}: {content}")
    transcript = "\n".join(transcript_lines)

    return f"""Analyze the following practice interview transcript and evaluate the candidate's performance.

Role: {role_title or 'Not specified'}
Job Description: {job_description or 'Not specified'}

--- TRANSCRIPT ---
{transcript}
--- END TRANSCRIPT ---

Evaluate the candidate on communication, technical knowledge, problem solving, confidence, and relevance. Provide an overall score, specific strengths, areas for improvement, detailed feedback, and per-question scores.

IMPORTANT: Use the full 1-10 scoring range. Do NOT give the same score for every question. Differentiate based on quality — strong, specific answers with examples should score higher (7-10) than vague or incomplete answers (1-5). Be honest and critical."""


def _call_nova_lite(prompt: str) -> dict:
    """Call Amazon Nova Lite via Bedrock Converse API with structured output.
    Uses toolConfig with constrained decoding for guaranteed schema compliance.
    Runs in a thread executor from the async caller."""
    region = os.getenv("AWS_REGION", "us-east-1")
    client = boto3.client("bedrock-runtime", region_name=region)

    response = client.converse(
        modelId="amazon.nova-lite-v1:0",
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
    """Original heuristic scoring as a fallback."""
    user_messages = [m[1] for m in messages_data if m[0] == "USER"]
    assistant_messages = [m[1] for m in messages_data if m[0] == "ASSISTANT"]

    total_user_words = sum(len(m.split()) for m in user_messages)
    avg_response_length = total_user_words / max(len(user_messages), 1)

    communication = min(10, max(3, avg_response_length / 5))
    technical_keywords = ["algorithm", "system", "design", "implement", "optimize", "database", "api", "architecture", "scale", "performance", "complexity", "data structure"]
    tech_count = sum(1 for m in user_messages for k in technical_keywords if k in m.lower())
    technical = min(10, max(3, 3 + tech_count * 0.8))
    structure_keywords = ["first", "then", "because", "approach", "solution", "consider", "trade-off", "alternatively"]
    struct_count = sum(1 for m in user_messages for k in structure_keywords if k in m.lower())
    problem_solving = min(10, max(3, 3 + struct_count * 0.7))
    hedge_words = ["maybe", "i think", "probably", "not sure", "i guess", "kind of", "sort of"]
    hedge_count = sum(1 for m in user_messages for k in hedge_words if k in m.lower())
    confidence = min(10, max(3, 8 - hedge_count * 0.5))
    questions = [m for m in assistant_messages if m.strip().endswith("?")]
    relevance = min(10, max(3, 5 + min(len(user_messages), len(questions)) * 0.5))
    overall = round((communication + technical + problem_solving + confidence + relevance) / 5, 1)

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

    question_scores = []
    for i, (role, content) in enumerate(messages_data):
        if role == "ASSISTANT" and content.strip().endswith("?"):
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
            f"Interview session completed with {len(user_messages)} responses to {len(questions)} questions. "
            f"Average response length: {int(avg_response_length)} words."
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

    # Get session context for the AI prompt
    session_result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session_row = session_result.scalar_one_or_none()
    job_description = session_row.job_description if session_row else ""
    role_title = session_row.role_title if session_row else ""

    # Try AI scoring, fall back to heuristic
    scores = None
    try:
        prompt = _build_scoring_prompt(messages_data, job_description, role_title)
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
        strengths=json.dumps(scores.get("strengths", [])),
        improvements=json.dumps(scores.get("improvements", [])),
        detailed_feedback=scores.get("detailed_feedback", ""),
        question_scores=json.dumps(scores.get("question_scores", [])),
        created_at=now,
    )
    db.add(new_score)
    await db.commit()
