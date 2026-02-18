from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


# ─── Auth Models ────────────────────────────────────────────

class UserRegister(BaseModel):
    email: str
    name: str
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    resume_text: str
    resume_filename: str
    created_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ─── Session Models ─────────────────────────────────────────

class SessionCreate(BaseModel):
    name: str
    job_description: str
    company_name: Optional[str] = ""
    role_title: Optional[str] = ""


class SessionResponse(BaseModel):
    id: str
    user_id: str
    name: str
    job_description: str
    company_name: str
    role_title: str
    interviewer_name: Optional[str] = ""
    interviewer_voice: Optional[str] = ""
    status: str
    created_at: str
    completed_at: Optional[str] = None


class SessionListResponse(BaseModel):
    sessions: List[SessionResponse]


# ─── Message Models ─────────────────────────────────────────

class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    timestamp: str


# ─── Score Models ────────────────────────────────────────────

class QuestionScore(BaseModel):
    question: str
    answer_summary: str
    score: float
    feedback: str


class SessionScoreResponse(BaseModel):
    id: str
    session_id: str
    overall_score: float
    communication_score: float
    technical_score: float
    problem_solving_score: float
    confidence_score: float
    relevance_score: float
    strengths: List[str]
    improvements: List[str]
    detailed_feedback: str
    question_scores: List[QuestionScore]
    created_at: str


# ─── Analytics Models ───────────────────────────────────────

class SessionAnalytics(BaseModel):
    session: SessionResponse
    score: Optional[SessionScoreResponse] = None
    messages: List[MessageResponse]
    total_questions: int
    total_duration_seconds: Optional[float] = None
