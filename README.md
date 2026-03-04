<p align="center">
  <h1 align="center">Interviewed — Backend</h1>
  <p align="center">AI-powered real-time interview platform built with FastAPI & Amazon Nova Sonic</p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=flat&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-AsyncPG-4169E1?style=flat&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/AWS-Nova%20Sonic-FF9900?style=flat&logo=amazonaws&logoColor=white" />
</p>

---

### 📦 Repositories

[![Frontend](https://img.shields.io/badge/Frontend-Next.js%2016-000000?style=flat&logo=next.js)](https://github.com/Amaru333/interviewed-frontend)
[![Backend](https://img.shields.io/badge/Backend-FastAPI-009688?style=flat&logo=fastapi)](https://github.com/Amaru333/interviewed-backend)

| Repository   | Link                                                                                                           |
| ------------ | -------------------------------------------------------------------------------------------------------------- |
| **Frontend** | [github.com/Amaru333/interviewed-frontend](https://github.com/Amaru333/interviewed-frontend) |
| **Backend**  | [github.com/Amaru333/interviewed-backend](https://github.com/Amaru333/interviewed-backend)   |

---

## ✨ Features

- **Real-Time AI Interviewing** — WebSocket-based bidirectional audio streaming via Amazon Nova Sonic with multi-panelist support
- **Auto-Reconnect & History Replay** — Transparent session recovery with full conversation context preservation
- **Live Code Evaluation** — Real-time code challenge delivery and submission processing during interviews
- **Recruiter Portal** — Job management, bulk candidate invitations with expiry, and email notifications via SMTP
- **Scoring & Analytics** — AI-generated scoring across communication, technical, problem-solving, confidence, and relevance dimensions
- **Resume Processing** — PDF parsing for personalized AI interview context
- **Auth System** — JWT-based authentication with role separation (candidate & recruiter)

---

## 🏗️ Architecture

```
backend/
├── main.py                      # FastAPI app, WebSocket handler, InterviewConnectionManager
├── database.py                  # SQLAlchemy async models (User, Session, Message, etc.)
├── models.py                    # Pydantic request/response schemas
├── auth.py                      # JWT authentication utilities
├── email_service.py             # SMTP email service for interview invites
├── interview_nova_sonic.py      # Amazon Nova Sonic streaming client
├── routes/
│   ├── auth_routes.py           # Login, register, user profile
│   ├── session_routes.py        # Interview sessions, scoring, analytics
│   ├── recruiter_routes.py      # Jobs, invites, candidate pipeline
│   └── resume_routes.py         # Resume upload & parsing
├── .github/workflows/
│   └── deploy.yml               # GitHub Actions → EC2 deployment
└── requirements.txt
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.12+
- PostgreSQL (or SQLite for local dev)
- AWS credentials with Bedrock access

### Setup

```bash
# Clone the repository
git clone https://github.com/Amaru333/interviewed-backend.git
cd interviewed-backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Run the server
python main.py
```

The API will be available at `http://localhost:8000`.

---

## ⚙️ Environment Variables

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | Database connection string | `sqlite+aiosqlite:///./interviewed.db` |
| `AWS_REGION` | AWS region for Bedrock | `us-east-1` |
| `AWS_ACCESS_KEY_ID` | AWS access key | — |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | — |
| `SECRET_KEY` | JWT signing key | Auto-generated |
| `SMTP_HOST` | SMTP server hostname | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP server port | `587` |
| `SMTP_USER` | SMTP username/email | — |
| `SMTP_PASSWORD` | SMTP password (app password) | — |
| `FRONTEND_URL` | Frontend URL for invite links | `http://localhost:3000` |

---

## 🔌 API Overview

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/register` | Register a new user |
| `POST` | `/auth/login` | Login and receive JWT |
| `GET` | `/auth/me` | Get current user profile |
| `WS` | `/ws/interview/{session_id}` | Real-time interview WebSocket |
| `POST` | `/sessions` | Create a new interview session |
| `GET` | `/sessions` | List user's sessions |
| `GET` | `/sessions/{id}/results` | Get session results & scores |
| `POST` | `/resume/upload` | Upload and parse resume PDF |
| `POST` | `/recruiter/register` | Register recruiter account |
| `POST` | `/recruiter/jobs` | Create a job listing |
| `POST` | `/recruiter/jobs/{id}/invite` | Bulk invite candidates |

---

## 🚢 Deployment

The backend deploys to **AWS EC2** via GitHub Actions. Deployment is triggered by creating a GitHub release or manually via the Actions tab.

### GitHub Secrets Required

| Secret | Description |
|---|---|
| `EC2_SSH_KEY` | SSH private key for EC2 |
| `EC2_HOST` | EC2 instance IP address |
| `EC2_USER` | EC2 username (`ubuntu`) |
| `DATABASE_URL` | Production database URL |
| `AWS_REGION` | AWS region |
| `AWS_ACCESS_KEY_ID` | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |

### Deploy

```bash
# Push to main and create a release tag
git tag v1.0.0
git push origin v1.0.0
# → GitHub Actions handles the rest
```

On EC2, the app runs via PM2:

```bash
pm2 logs interviewed-backend     # View logs
pm2 restart interviewed-backend  # Restart
pm2 status                       # Check status
```

---

## 📄 License

This project is part of the Interviewed platform.
