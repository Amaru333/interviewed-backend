# URGENT: EC2 Backend Not Set Up

## The Problem

Your EC2 instance **doesn't have the backend code yet**. The GitHub Actions workflow is trying to deploy to a directory that doesn't exist.

## Quick Fix (5 minutes)

### Step 1: SSH to Your EC2

```bash
ssh -i ~/Downloads/interviewed-key.pem ubuntu@YOUR_EC2_IP
```

### Step 2: Clone Your Backend Repository

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/interviewed-backend.git interviewed-backend
cd interviewed-backend
```

### Step 3: Set Up Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 4: Create .env File

```bash
nano .env
```

Paste this (replace with your actual values):
```env
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
```

Save: `Ctrl+X`, `Y`, `Enter`

### Step 5: Create Start Script

```bash
cat > start.sh << 'EOF'
#!/bin/bash
cd /home/ubuntu/interviewed-backend
source venv/bin/activate
python main.py
EOF

chmod +x start.sh
```

### Step 6: Start with PM2

```bash
pm2 start start.sh --name interviewed-backend
pm2 save
pm2 startup
```

### Step 7: Test

```bash
curl http://localhost:8000/api/health
```

You should see: `{"status":"ok","service":"interviewed","uptime":...,"version":"1.0.0"}`

## Automated Setup (Alternative)

I've created a setup script for you. On your EC2:

```bash
# Download the setup script
cd ~
curl -O https://raw.githubusercontent.com/YOUR_USERNAME/interviewed-backend/main/ec2-setup.sh

# Make it executable
chmod +x ec2-setup.sh

# Run it
./ec2-setup.sh
```

## After Setup

Once the backend is set up on EC2, your GitHub Actions deployments will work automatically!

Just create a release and it will:
1. ✅ Find the `interviewed-backend` directory
2. ✅ Pull latest code
3. ✅ Install dependencies
4. ✅ Restart the app

## Verify Setup

After setup, verify everything is working:

```bash
# Check PM2 status
pm2 status

# Check logs
pm2 logs interviewed-backend

# Test API
curl http://localhost:8000/api/health
```

---

**Do this now, then create a new release to test deployment!** 🚀
