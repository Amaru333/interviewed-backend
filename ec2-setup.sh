#!/bin/bash
# EC2 Initial Setup Script for Backend
# Run this ONCE on your EC2 instance to set up the backend

set -e

echo "🚀 Setting up backend on EC2..."

# Update system
echo "📦 Updating system packages..."
sudo apt update
sudo apt upgrade -y

# Install required packages
echo "📦 Installing required packages..."
sudo apt install -y python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx

# Install Node.js and PM2
echo "📦 Installing Node.js and PM2..."
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g pm2

# Clone backend repository
echo "📥 Cloning backend repository..."
cd ~
if [ -d "interviewed-backend" ]; then
  echo "⚠️  Backend directory already exists, skipping clone..."
else
  read -p "Enter your GitHub repository URL (e.g., https://github.com/username/interviewed-backend.git): " REPO_URL
  git clone "$REPO_URL" interviewed-backend
fi

cd ~/interviewed-backend

# Set up Python virtual environment
echo "🐍 Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "📦 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file
echo "🔐 Creating .env file..."
if [ ! -f .env ]; then
  cat > .env << 'EOF'
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
EOF
  echo "⚠️  Please edit .env file with your actual credentials:"
  echo "    nano ~/interviewed-backend/.env"
else
  echo "✅ .env file already exists"
fi

# Create start script
echo "📝 Creating start script..."
cat > start.sh << 'EOF'
#!/bin/bash
cd /home/ubuntu/interviewed-backend
source venv/bin/activate
python main.py
EOF
chmod +x start.sh

# Start application with PM2
echo "🚀 Starting application with PM2..."
pm2 delete interviewed-backend 2>/dev/null || true
pm2 start start.sh --name interviewed-backend
pm2 save
pm2 startup

echo ""
echo "✅ Setup complete!"
echo ""
echo "📋 Next steps:"
echo "1. Edit .env file: nano ~/interviewed-backend/.env"
echo "2. Restart app: pm2 restart interviewed-backend"
echo "3. Check status: pm2 status"
echo "4. View logs: pm2 logs interviewed-backend"
echo ""
echo "🔗 Your API should be running at: http://$(curl -s ifconfig.me):8000"
echo "🧪 Test it: curl http://localhost:8000/api/health"
