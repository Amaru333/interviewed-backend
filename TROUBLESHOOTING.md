# 🔧 Troubleshooting Deployment & HTTPS Setup

## Issue 1: Code Changes Not Showing After Deployment

Your GitHub Actions succeeded, but the changes aren't visible. Here's how to debug:

### Step 1: Check What's Running on EC2

SSH to your EC2:
```bash
ssh -i ~/Downloads/interviewed-key.pem ubuntu@YOUR_IP
```

Check PM2 status:
```bash
pm2 status
pm2 logs interviewed-backend --lines 50
```

### Step 2: Verify Git Pull Worked

```bash
cd ~/backend
git log --oneline -5
git status
```

Check if the latest commit is there. If not, the git pull might have failed.

### Step 3: Check the Code

```bash
grep -n "uptime" ~/backend/main.py
```

You should see:
```python
return {"status": "ok", "service": "interviewed", "uptime": time.time()}
```

If you don't see it, the code wasn't pulled.

### Step 4: Manual Fix (Quick)

If the code isn't updated:

```bash
cd ~/backend
git pull origin main
pm2 restart interviewed-backend
```

Then test:
```bash
curl http://localhost:8000/api/health
```

### Common Causes:

**Problem 1: Git Pull Failed**
- **Cause**: Uncommitted changes on EC2
- **Solution**:
  ```bash
  cd ~/backend
  git stash
  git pull origin main
  pm2 restart interviewed-backend
  ```

**Problem 2: PM2 Not Restarting Properly**
- **Cause**: PM2 restart command failed silently
- **Solution**:
  ```bash
  pm2 delete interviewed-backend
  pm2 start ~/backend/start.sh --name interviewed-backend
  pm2 save
  ```

**Problem 3: Wrong Directory**
- **Cause**: Code is in different location
- **Solution**: Check where PM2 is running from:
  ```bash
  pm2 info interviewed-backend
  ```

### Step 5: Update Deployment Workflow

Add better error handling to the workflow. Update `.github/workflows/deploy.yml`:

```yaml
- name: 📤 Deploy to EC2
  run: |
    ssh -i ~/.ssh/deploy_key -o StrictHostKeyChecking=no ${{ secrets.EC2_USER }}@${{ secrets.EC2_HOST }} << 'ENDSSH'
      set -e  # Exit on error
      
      cd ~/backend
      
      # Stash any local changes
      git stash
      
      # Pull latest changes
      git pull origin main
      
      # Verify the change is there
      echo "Checking for uptime in health endpoint..."
      grep -q "uptime" main.py && echo "✅ Code updated!" || echo "❌ Code NOT updated!"
      
      # Activate virtual environment
      source venv/bin/activate
      
      # Install/update dependencies
      pip install -r requirements.txt
      
      # Set up environment variables
      cat > .env << EOF
    DATABASE_URL=${{ secrets.DATABASE_URL }}
    AWS_REGION=${{ secrets.AWS_REGION }}
    AWS_ACCESS_KEY_ID=${{ secrets.AWS_ACCESS_KEY_ID }}
    AWS_SECRET_ACCESS_KEY=${{ secrets.AWS_SECRET_ACCESS_KEY }}
    EOF
      
      # Force restart (delete and recreate)
      pm2 delete interviewed-backend || true
      pm2 start start.sh --name interviewed-backend
      pm2 save
      
      # Show status
      pm2 status
      pm2 logs interviewed-backend --lines 20
    ENDSSH
```

---

## Issue 2: Setting Up HTTPS

Your backend is on HTTP, which causes security warnings. Let's set up HTTPS!

### Prerequisites:
- ✅ A domain name (e.g., `api.yourdomain.com`)
- ✅ Domain pointing to your EC2 IP address

### Option A: With a Domain (Recommended)

#### Step 1: Point Domain to EC2

In your domain provider (Namecheap, GoDaddy, etc.):
1. Create an **A Record**
2. Name: `api` (or `@` for root)
3. Value: Your EC2 IP address
4. TTL: 300

Wait 5-10 minutes for DNS to propagate.

#### Step 2: Install Nginx

SSH to EC2:
```bash
ssh -i ~/Downloads/interviewed-key.pem ubuntu@YOUR_IP

# Install Nginx
sudo apt update
sudo apt install nginx -y
```

#### Step 3: Configure Nginx

```bash
sudo nano /etc/nginx/sites-available/interviewed
```

Paste this (replace `api.yourdomain.com`):
```nginx
server {
    listen 80;
    server_name api.yourdomain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}
```

Save and exit (`Ctrl+X`, `Y`, `Enter`).

#### Step 4: Enable the Site

```bash
sudo ln -s /etc/nginx/sites-available/interviewed /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

#### Step 5: Install SSL Certificate (Let's Encrypt)

```bash
# Install Certbot
sudo apt install certbot python3-certbot-nginx -y

# Get SSL certificate (replace with your domain)
sudo certbot --nginx -d api.yourdomain.com
```

Follow the prompts:
- Enter your email
- Agree to terms
- Choose to redirect HTTP to HTTPS (option 2)

**Done!** Your API is now on HTTPS! 🎉

Test: `https://api.yourdomain.com/api/health`

#### Step 6: Auto-Renewal

Certbot automatically sets up renewal. Test it:
```bash
sudo certbot renew --dry-run
```

### Option B: Without a Domain (Self-Signed Certificate)

If you don't have a domain, you can use a self-signed certificate (browsers will still warn, but it's encrypted):

```bash
# Generate self-signed certificate
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/ssl/private/nginx-selfsigned.key \
  -out /etc/ssl/certs/nginx-selfsigned.crt

# Configure Nginx
sudo nano /etc/nginx/sites-available/interviewed
```

Paste:
```nginx
server {
    listen 443 ssl;
    server_name YOUR_EC2_IP;

    ssl_certificate /etc/ssl/certs/nginx-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/nginx-selfsigned.key;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}

server {
    listen 80;
    server_name YOUR_EC2_IP;
    return 301 https://$server_name$request_uri;
}
```

Enable and restart:
```bash
sudo ln -s /etc/nginx/sites-available/interviewed /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### Option C: Use AWS Application Load Balancer (Advanced)

AWS ALB can handle SSL termination:
1. Create an ALB in AWS Console
2. Add SSL certificate from AWS Certificate Manager
3. Point ALB to your EC2 instance
4. Update security groups

---

## Quick Commands Reference

### Check if changes deployed:
```bash
ssh -i ~/Downloads/interviewed-key.pem ubuntu@YOUR_IP
cd ~/backend
git log --oneline -1
grep "uptime" main.py
curl http://localhost:8000/api/health
```

### Force redeploy:
```bash
cd ~/backend
git stash
git pull origin main
pm2 delete interviewed-backend
pm2 start start.sh --name interviewed-backend
pm2 save
```

### Check HTTPS status:
```bash
curl -I https://api.yourdomain.com/api/health
```

### Renew SSL certificate:
```bash
sudo certbot renew
```

---

## Summary

**For deployment issue:**
1. SSH to EC2 and manually check if code is updated
2. Run `git pull` and `pm2 restart` manually
3. Update workflow to add `git stash` and better error handling

**For HTTPS:**
1. Get a domain name (recommended)
2. Install Nginx
3. Get free SSL certificate with Let's Encrypt
4. Done! 🎉

Need help with any specific step? Let me know!
