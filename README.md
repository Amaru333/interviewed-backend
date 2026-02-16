# 🚀 Backend Deployment Setup

This backend is set up as a **separate repository** with its own GitHub Actions workflow.

## Quick Setup

### 1. Initialize Git Repository

```bash
cd /Users/amaru/Desktop/interviewed/backend

# Initialize git if not already done
git init

# Add all files
git add .

# Create initial commit
git commit -m "Initial backend setup"

# Create GitHub repository and push
# Go to github.com and create a new repository called "interviewed-backend"
git remote add origin https://github.com/YOUR_USERNAME/interviewed-backend.git
git branch -M main
git push -u origin main
```

### 2. Add GitHub Secrets

Go to: `https://github.com/YOUR_USERNAME/interviewed-backend/settings/secrets/actions`

Add these 7 secrets:

| Secret Name | Description |
|-------------|-------------|
| `EC2_SSH_KEY` | Your SSH private key for EC2 |
| `EC2_HOST` | Your EC2 IP address (e.g., `54.123.45.67`) |
| `EC2_USER` | EC2 username (usually `ubuntu`) |
| `DATABASE_URL` | Database connection string |
| `AWS_REGION` | AWS region (e.g., `us-east-1`) |
| `AWS_ACCESS_KEY_ID` | Your AWS access key |
| `AWS_SECRET_ACCESS_KEY` | Your AWS secret key |

### 3. Deploy

**Option A: Create a Release (Recommended)**
1. Go to: `https://github.com/YOUR_USERNAME/interviewed-backend/releases`
2. Click "Create a new release"
3. Tag: `v1.0.0`
4. Click "Publish release"
5. GitHub Actions automatically deploys! 🎉

**Option B: Manual Trigger**
1. Go to: `https://github.com/YOUR_USERNAME/interviewed-backend/actions`
2. Click "Deploy to AWS EC2"
3. Click "Run workflow"
4. Click green "Run workflow" button

## EC2 Setup

On your EC2 instance, the backend should be in `~/backend`:

```bash
ssh -i ~/Downloads/interviewed-key.pem ubuntu@YOUR_IP

# Clone your backend repository
cd ~
git clone https://github.com/YOUR_USERNAME/interviewed-backend.git backend
cd backend

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create start script
cat > start.sh << 'EOF'
#!/bin/bash
cd /home/ubuntu/backend
source venv/bin/activate
python main.py
EOF

chmod +x start.sh

# Start with PM2
pm2 start start.sh --name interviewed-backend
pm2 save
pm2 startup
```

## Directory Structure

```
backend/
├── .github/
│   └── workflows/
│       └── deploy.yml          # GitHub Actions workflow
├── .env.example                # Environment variables template
├── .gitignore                  # Git ignore file
├── main.py                     # Main application
├── database.py                 # Database models
├── auth.py                     # Authentication
├── interview_nova_sonic.py     # Nova Sonic integration
├── requirements.txt            # Python dependencies
├── routes/                     # API routes
│   ├── auth_routes.py
│   ├── session_routes.py
│   └── resume_routes.py
└── README.md                   # This file
```

## Environment Variables

Create a `.env` file for local development:

```bash
cp .env.example .env
nano .env
```

Add your credentials:
```env
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
```

## Development

```bash
# Activate virtual environment
source venv/bin/activate

# Run the app
python main.py

# Visit: http://localhost:8000
```

## Deployment Workflow

1. Make changes to your code
2. Commit and push:
   ```bash
   git add .
   git commit -m "Your changes"
   git push origin main
   ```
3. Create a release on GitHub
4. GitHub Actions automatically deploys to EC2
5. Done! 🎉

## Troubleshooting

**Problem: GitHub Actions can't connect to EC2**
- Check all 7 secrets are added
- Verify EC2 is running
- Check security group allows SSH (port 22)

**Problem: App won't start**
- SSH to EC2: `ssh -i ~/.ssh/deploy_key ubuntu@YOUR_IP`
- Check logs: `pm2 logs interviewed-backend`
- Check env vars: `cat ~/backend/.env`

**Problem: Database connection failed**
- Verify `DATABASE_URL` in GitHub Secrets
- Check database allows connections from EC2 IP
- Install correct driver: `pip install asyncpg` (PostgreSQL) or `pip install aiomysql` (MySQL)

## Documentation

- See `DATABASE_MIGRATION_GUIDE.md` for database setup
- See `DEPLOYMENT_SUMMARY.md` for quick reference
- See `AWS_DEPLOYMENT_GUIDE.md` for full AWS setup

---

**Ready to deploy?** Just create a GitHub release! 🚀
