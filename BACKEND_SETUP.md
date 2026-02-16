# 📦 Backend Repository Setup Guide

Your backend is now configured as a **separate repository** with its own GitHub Actions workflow!

## What's Different?

- ✅ GitHub Actions workflow is in `backend/.github/workflows/`
- ✅ Backend can be deployed independently
- ✅ Separate git repository for backend code
- ✅ All paths updated to work from backend root

---

## Quick Setup Steps

### 1. Initialize Backend Repository

```bash
cd /Users/amaru/Desktop/interviewed/backend

# Initialize git (if not already done)
git init

# Add all files
git add .

# Create initial commit
git commit -m "Initial backend setup with GitHub Actions"
```

### 2. Create GitHub Repository

1. Go to [github.com/new](https://github.com/new)
2. Repository name: `interviewed-backend`
3. Description: "Backend API for Interviewed - AI Interview Practice Platform"
4. Make it **Private** (recommended)
5. **Don't** initialize with README (you already have one)
6. Click "Create repository"

### 3. Push to GitHub

```bash
# Add remote (replace YOUR_USERNAME)
git remote add origin https://github.com/YOUR_USERNAME/interviewed-backend.git

# Rename branch to main
git branch -M main

# Push to GitHub
git push -u origin main
```

### 4. Add GitHub Secrets

Go to: `https://github.com/YOUR_USERNAME/interviewed-backend/settings/secrets/actions`

Click "New repository secret" for each:

```
EC2_SSH_KEY          = (your SSH private key)
EC2_HOST             = 54.123.45.67
EC2_USER             = ubuntu
DATABASE_URL         = postgresql+asyncpg://user:pass@host:5432/db
AWS_REGION           = us-east-1
AWS_ACCESS_KEY_ID    = (your AWS key)
AWS_SECRET_ACCESS_KEY = (your AWS secret)
```

### 5. Set Up EC2

SSH to your EC2 instance:

```bash
ssh -i ~/Downloads/interviewed-key.pem ubuntu@YOUR_IP
```

Clone your backend repository:

```bash
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

### 6. Deploy!

**Create a release:**
1. Go to: `https://github.com/YOUR_USERNAME/interviewed-backend/releases`
2. Click "Create a new release"
3. Tag: `v1.0.0`
4. Title: `v1.0.0 - Initial Release`
5. Click "Publish release"

GitHub Actions will automatically deploy! 🎉

---

## Files Created

```
backend/
├── .github/
│   └── workflows/
│       └── deploy.yml          ← GitHub Actions workflow
├── .env.example                ← Environment variables template
├── .gitignore                  ← Git ignore file
├── README.md                   ← Backend documentation
├── DATABASE_MIGRATION_GUIDE.md ← Database setup guide
├── DEPLOYMENT_SUMMARY.md       ← Quick reference
└── (all your existing backend files)
```

---

## Daily Workflow

```bash
cd /Users/amaru/Desktop/interviewed/backend

# Make changes to your code
nano main.py

# Commit and push
git add .
git commit -m "Fixed bug in interview logic"
git push origin main

# Create a release on GitHub
# → Automatically deploys to EC2! 🚀
```

---

## Verification

Check that everything is set up correctly:

1. ✅ Backend repository created on GitHub
2. ✅ Code pushed to GitHub
3. ✅ All 7 GitHub Secrets added
4. ✅ EC2 has backend cloned in `~/backend`
5. ✅ PM2 running the app
6. ✅ GitHub Actions workflow visible in "Actions" tab

---

## Next Steps

1. Create your first release to test deployment
2. Set up your online database (see `DATABASE_MIGRATION_GUIDE.md`)
3. Update frontend to point to your backend API URL

---

**You're all set!** Your backend is now a standalone repository with automated deployment! 🎉
