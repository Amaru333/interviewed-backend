# 🔐 Adding Environment Variables to AWS EC2

There are **3 ways** to add environment variables to your EC2 instance. Choose the method that works best for you.

---

## Method 1: Create a `.env` File (Recommended)

This is the easiest and most common approach.

### Step 1: Connect to EC2

```bash
ssh -i ~/Downloads/interviewed-key.pem ubuntu@YOUR_IP_ADDRESS
```

### Step 2: Navigate to Your Backend

```bash
cd ~/backend
```

### Step 3: Create `.env` File

```bash
nano .env
```

### Step 4: Add Your Variables

Paste this and replace with your actual values:

```env
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
```

### Step 5: Save and Exit

- Press `Ctrl+X`
- Press `Y` to confirm
- Press `Enter`

### Step 6: Verify

```bash
cat .env
```

You should see your environment variables!

### Step 7: Restart Your App

```bash
pm2 restart interviewed-backend
```

**Done!** Your app will now load these variables automatically (because we added `python-dotenv` support).

---

## Method 2: System-Wide Environment Variables

If you want environment variables available to all processes:

### Step 1: Edit Profile

```bash
nano ~/.bashrc
```

### Step 2: Add Variables at the End

```bash
export DATABASE_URL="postgresql+asyncpg://user:password@host:5432/database"
export AWS_REGION="us-east-1"
export AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"
export AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
```

### Step 3: Save and Reload

```bash
# Save (Ctrl+X, Y, Enter)

# Reload the profile
source ~/.bashrc
```

### Step 4: Verify

```bash
echo $DATABASE_URL
```

Should print your database URL!

### Step 5: Restart PM2

```bash
pm2 restart interviewed-backend
```

---

## Method 3: PM2 Ecosystem File (Advanced)

Use PM2's built-in environment variable management:

### Step 1: Create Ecosystem File

```bash
cd ~/backend
nano ecosystem.config.js
```

### Step 2: Add Configuration

```javascript
module.exports = {
  apps: [{
    name: 'interviewed-backend',
    script: './start.sh',
    env: {
      DATABASE_URL: 'postgresql+asyncpg://user:password@host:5432/database',
      AWS_REGION: 'us-east-1',
      AWS_ACCESS_KEY_ID: 'AKIAIOSFODNN7EXAMPLE',
      AWS_SECRET_ACCESS_KEY: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'
    }
  }]
}
```

### Step 3: Save and Exit

### Step 4: Restart with Ecosystem File

```bash
pm2 delete interviewed-backend
pm2 start ecosystem.config.js
pm2 save
```

---

## 🤖 Automatic Method (GitHub Actions)

**Good news!** Your GitHub Actions workflow already does this automatically!

When you deploy via GitHub release, the workflow creates the `.env` file for you:

```yaml
# Set up environment variables
cat > .env << EOF
DATABASE_URL=${{ secrets.DATABASE_URL }}
AWS_REGION=${{ secrets.AWS_REGION }}
AWS_ACCESS_KEY_ID=${{ secrets.AWS_ACCESS_KEY_ID }}
AWS_SECRET_ACCESS_KEY=${{ secrets.AWS_SECRET_ACCESS_KEY }}
EOF
```

So if you've added GitHub Secrets, you don't need to do anything manually! Just deploy via release.

---

## Which Method Should You Use?

| Method | When to Use | Pros | Cons |
|--------|-------------|------|------|
| **`.env` file** | Most cases | Easy, secure, standard | File-based |
| **System-wide** | Multiple apps | Available everywhere | Less secure |
| **PM2 ecosystem** | Complex setups | PM2-managed | More config |
| **GitHub Actions** | Production | Automated, secure | Requires GitHub |

**Recommendation**: Use **Method 1 (.env file)** for manual setup, or let **GitHub Actions** handle it automatically.

---

## Security Best Practices

### ✅ DO:
- Use `.env` file (it's in `.gitignore`)
- Set proper file permissions: `chmod 600 .env`
- Use GitHub Secrets for CI/CD
- Rotate credentials regularly

### ❌ DON'T:
- Commit `.env` to git
- Share credentials in plain text
- Use same credentials for dev/prod
- Store credentials in code

---

## Verify Environment Variables Are Loaded

### Check if .env file exists:
```bash
ls -la ~/backend/.env
```

### Check if app can read them:
```bash
cd ~/backend
source venv/bin/activate
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('DATABASE_URL:', os.getenv('DATABASE_URL'))"
```

### Check PM2 logs:
```bash
pm2 logs interviewed-backend --lines 50
```

Look for any errors about missing environment variables.

---

## Troubleshooting

### Problem: Variables not loading

**Solution:**
```bash
# Make sure python-dotenv is installed
cd ~/backend
source venv/bin/activate
pip install python-dotenv
pip freeze > requirements.txt

# Make sure load_dotenv() is called in main.py
grep -n "load_dotenv" main.py

# Restart the app
pm2 restart interviewed-backend
```

### Problem: Permission denied

**Solution:**
```bash
# Set correct permissions
chmod 600 ~/backend/.env
chown ubuntu:ubuntu ~/backend/.env
```

### Problem: .env file disappears after deployment

**Solution:**
- Make sure `.env` is in `.gitignore`
- Use GitHub Actions to recreate it on each deploy
- Or use Method 2 (system-wide) for persistence

---

## Quick Reference

### Add/Update Variables:
```bash
ssh -i ~/Downloads/interviewed-key.pem ubuntu@YOUR_IP
cd ~/backend
nano .env
# Add your variables
pm2 restart interviewed-backend
```

### View Current Variables:
```bash
cat ~/backend/.env
```

### Test Variables:
```bash
cd ~/backend
source venv/bin/activate
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.environ)"
```

---

**That's it!** Your environment variables are now set up on EC2! 🎉
