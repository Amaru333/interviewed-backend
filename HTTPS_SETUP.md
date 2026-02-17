# 🔒 HTTPS Setup Guide for api.interviewed.space

Since you have purchased the domain **api.interviewed.space**, this guide will show you how to set up a professional SSL certificate for free using Let's Encrypt and Nginx.

## Prerequisites
- ✅ Domain: `api.interviewed.space`
- ✅ EC2 Instance IP address (e.g., `54.123.45.67`)

---

## Step 1: Point Your Domain to EC2

1. Log in to your domain provider (where you bought `interviewed.space`).
2. Go to the **DNS Management** section.
3. Add an **A Record**:
   - **Host/Name**: `api`
   - **Value**: Your EC2 IP Address
   - **TTL**: 300 (or Automatic)
4. Save the changes and wait 5-10 minutes for the DNS to propagate.

**Test it:** 
Run `nslookup api.interviewed.space` in your terminal. It should return your EC2 IP.

---

## Step 2: Set Up Nginx on EC2

SSH to your EC2 instance:
```bash
ssh -i ~/Downloads/interviewed-key.pem ubuntu@YOUR_EC2_IP
```

1. **Install Nginx:**
   ```bash
   sudo apt update
   sudo apt install nginx -y
   ```

2. **Configure Nginx as a Reverse Proxy:**
   ```bash
   sudo nano /etc/nginx/sites-available/interviewed
   ```

   Paste this configuration:
   ```nginx
   server {
       listen 80;
       server_name api.interviewed.space;

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

3. **Enable the Configuration:**
   ```bash
   sudo ln -s /etc/nginx/sites-available/interviewed /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl restart nginx
   ```

---

## Step 3: Get Your Free SSL Certificate

We will use **Certbot** to get a free certificate from Let's Encrypt.

1. **Install Certbot:**
   ```bash
   sudo apt install certbot python3-certbot-nginx -y
   ```

2. **Run Certbot:**
   ```bash
   sudo certbot --nginx -d api.interviewed.space
   ```

3. **Follow the Prompts:**
   - Enter your email address.
   - Agree to the Terms of Service.
   - Choose **Option 2** (Redirect) to automatically redirect all HTTP traffic to HTTPS.

---

## Step 4: Update AWS Security Group

Your EC2 instance needs to allow traffic on port **443** (HTTPS).

1. Go to the **AWS EC2 Console**.
2. Select **Instances** -> Click on your instance.
3. Go to the **Security** tab -> Click on the **Security Group**.
4. Click **Edit inbound rules**.
5. Add a new rule:
   - **Type**: HTTPS
   - **Protocol**: TCP
   - **Port range**: 443
   - **Source**: `0.0.0.0/0` (Anywhere)
6. Save rules.

---

## Step 5: Update Frontend Configuration

Now that your backend is secure, update your frontend application to use the new HTTPS URL.

```javascript
// Change from:
// const API_BASE_URL = "http://54.123.45.67:8000";

// To:
const API_BASE_URL = "https://api.interviewed.space";
```

---

## Troubleshooting

### "Certificate not found" or "DNS not resolved"
Make sure your A record has fully propagated. You can check it on [whatsmydns.net](https://www.whatsmydns.net/#A/api.interviewed.space).

### "502 Bad Gateway"
This means Nginx is working, but it can't find your Python backend. Make sure your PM2 process is running:
```bash
pm2 status
```

### Auto-Renewal
Let's Encrypt certificates last for 90 days. Certbot handles renewal automatically. You can test it with:
```bash
sudo certbot renew --dry-run
```

---

**Congratulations! Your API is now live and secure at https://api.interviewed.space** 🔒🎉
