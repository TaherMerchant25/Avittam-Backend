# 🚀 Deploy MentorGold Python Backend

## Quick Deploy on Render

### Method 1: Using Render Dashboard

1. **Push to GitHub**:
   ```bash
   git remote add origin <your-python-backend-repo-url>
   git push -u origin main
   ```

2. **Deploy on Render**:
   - Go to [render.com](https://render.com)
   - Click **"New +"** → **"Web Service"**
   - Connect this GitHub repository
   - Render will auto-detect `render.yaml` and configure everything
   - Click **"Create Web Service"**

3. **Add Environment Variables** (in Render dashboard):
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `JWT_SECRET` (generate with: `openssl rand -hex 32`)
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `RAZORPAY_KEY_ID`
   - `RAZORPAY_KEY_SECRET`
   - `FRONTEND_URL` (your Vercel URL after frontend deployment)

### Method 2: Using render.yaml (Automatic)

The repository includes `render.yaml` which automatically configures:
- Python 3.11 runtime
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment variables template

Just connect the repo and Render does the rest!

## Environment Variables Required

```bash
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# JWT
JWT_SECRET=your-secret-key-min-32-chars
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24

# Google OAuth
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=https://your-app.vercel.app/auth/callback

# Razorpay
RAZORPAY_KEY_ID=rzp_live_xxxxx
RAZORPAY_KEY_SECRET=your-razorpay-secret

# CORS
FRONTEND_URL=https://your-app.vercel.app

# Optional
DEBUG=false
ENVIRONMENT=production
RATE_LIMIT_PER_MINUTE=60
```

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment variables
cp .env.example .env
# Edit .env with your values

# Run server
uvicorn app.main:app --reload --port 8000
```

API will be available at: http://localhost:8000/docs

## Features

- **FastAPI** - Modern Python web framework
- **Supabase** - PostgreSQL database
- **Google OAuth** - Mentor authentication
- **Razorpay** - Payment processing
- **Wallet System** - Rating-based earnings (1-5 stars)
- **MLM Commissions** - Multi-level referral system
  - 25% organization fee
  - 50% direct referrer
  - 12.5% level 2 referrer
  - 12.5% remaining upline referrers

## API Endpoints

- `GET /` - Health check
- `GET /docs` - Swagger UI documentation
- `GET /health` - Detailed health status
- `/api/mentors/*` - Mentor management
- `/api/sessions/*` - Session management
- `/api/wallets/*` - Wallet & transactions
- `/api/payments/*` - Payment processing
- `/api/meetings/*` - Google Meet integration
- `/api/notifications/*` - Notification system

## Support

For issues: Check Render logs in dashboard
