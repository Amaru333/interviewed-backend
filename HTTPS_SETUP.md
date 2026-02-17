# 🔒 Quick HTTPS Setup Guide

## Problem
Your backend is running on HTTP, causing security warnings in browsers.

## Solution: Set Up HTTPS with Let's Encrypt

### Prerequisites
- A domain name (e.g., `api.yourdomain.com`)
- Domain pointing to your EC2 IP address

---

## Step-by-Step Setup

### 1. Point Your Domain to EC2

In your domain provider (Namecheap, GoDaddy, Cloudflare, etc.):

1. Go to DNS settings
2. Add an **A Record**:
   - **Name**: `api` (or `@` for root domain)
   - **Value**: Your EC2 IP address (e.g., `54.123.45.67`)
   - **TTL**: 300 or Auto

3. Save and wait 5-10 minutes for DNS propagation

**Test DNS:**
```bash
nslookup api.yourdomain.com
# Should return your EC2 IP
```

### 2. Install Nginx on EC2

SSH to your EC2:
```bash
ssh -i ~/Downloads/interviewed-key.pem ubuntu@YOUR_EC2_IP
```

Install Nginx:
```bash
sudo apt update
sudo apt install nginx -y
```

### 3. Configure Nginx

Create Nginx configuration:
```bash
sudo nano /etc/nginx/sites-available/interviewed
```

Paste this (replace `api.yourdomain.com` with YOUR domain):
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

Save: `Ctrl+X`, `Y`, `Enter`

Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/interviewed /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

Test: Visit `http://api.yourdomain.com/api/health`

### 4. Install SSL Certificate (Let's Encrypt)

Install Certbot:
```bash
sudo apt install certbot python3-certbot-nginx -y
```

Get FREE SSL certificate:
```bash
sudo certbot --nginx -d api.yourdomain.com
```

Follow the prompts:
- Enter your email
- Agree to terms (Y)
- Share email? (N)
- Redirect HTTP to HTTPS? **Choose 2** (Yes, redirect)

**Done!** 🎉

### 5. Test HTTPS

Visit: `https://api.yourdomain.com/api/health`

You should see the lock icon! 🔒

### 6. Auto-Renewal

Certbot automatically renews. Test it:
```bash
sudo certbot renew --dry-run
```

---

## Update Security Group

Make sure your EC2 security group allows HTTPS:

1. Go to AWS Console → EC2 → Security Groups
2. Select your security group
3. Add inbound rule:
   - **Type**: HTTPS
   - **Port**: 443
   - **Source**: 0.0.0.0/0
4. Save

---

## Update Frontend

Update your frontend to use HTTPS:

```javascript
// Before
const API_URL = "http://54.123.45.67:8000";

// After
const API_URL = "https://api.yourdomain.com";
```

---

## Troubleshooting

**Problem: Domain doesn't resolve**
```bash
# Check DNS
nslookup api.yourdomain.com
# Should show your EC2 IP
```

**Problem: Certbot fails**
```bash
# Make sure Nginx is running
sudo systemctl status nginx

# Make sure domain points to EC2
curl -I http://api.yourdomain.com
```

**Problem: Still seeing HTTP warning**
- Clear browser cache
- Make sure you're visiting `https://` not `http://`
- Check certificate: Click lock icon in browser

---

## Summary

1. ✅ Point domain to EC2
2. ✅ Install Nginx
3. ✅ Configure Nginx as reverse proxy
4. ✅ Get free SSL certificate with Certbot
5. ✅ Update security group
6. ✅ Update frontend to use HTTPS

**Your API is now secure!** 🔒🎉
