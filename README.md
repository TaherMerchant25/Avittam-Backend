# MentorGold Python Backend (FastAPI)

Backend API for MentorGold - Session booking, Google Meet integration, and mentor management.

## Features

- 🚀 FastAPI with async support
- 🔐 JWT Authentication with Supabase
- 📅 Google Calendar & Meet integration
- 🔔 Real-time notifications
- 💳 Payment processing (Razorpay)
- 📊 Session management
- 👨‍🏫 Mentor/Mentee ping system

## Project Structure

```
python-backend/
├── app/
│   ├── main.py              # FastAPI application entry point
│   ├── config/
│   │   ├── database.py      # Supabase configuration
│   │   ├── google.py        # Google OAuth configuration
│   │   └── settings.py      # Environment settings
│   ├── models/
│   │   └── schemas.py       # Pydantic models/schemas
│   ├── routes/
│   │   ├── sessions.py      # Session endpoints
│   │   ├── mentors.py       # Mentor/ping endpoints
│   │   ├── meetings.py      # Google Meet endpoints
│   │   ├── notifications.py # Notification endpoints
│   │   └── payments.py      # Payment endpoints
│   ├── services/
│   │   ├── sessions.py      # Session business logic
│   │   ├── mentors.py       # Mentor business logic
│   │   ├── google_meet.py   # Google Meet integration
│   │   └── notifications.py # Notification logic
│   ├── middleware/
│   │   ├── auth.py          # Authentication middleware
│   │   └── error_handler.py # Error handling
│   └── utils/
│       └── helpers.py       # Utility functions
├── tests/
├── requirements.txt
├── .env.example
└── README.md
```

## Getting Started

### Prerequisites

- Python 3.10+
- Supabase account
- Google Cloud Console project (for Calendar/Meet)

### Installation

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
.\venv\Scripts\activate  # Windows
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Copy environment file and configure:
```bash
cp .env.example .env
```

4. Run the development server:
```bash
uvicorn app.main:app --reload --port 3001
```

### Environment Variables

See `.env.example` for required environment variables.

## API Documentation

Once running, access:
- Swagger UI: `http://localhost:3001/docs`
- ReDoc: `http://localhost:3001/redoc`
- OpenAPI JSON: `http://localhost:3001/openapi.json`

## Deployment

### Vercel (Serverless)

Create a `vercel.json`:
```json
{
  "builds": [{"src": "app/main.py", "use": "@vercel/python"}],
  "routes": [{"src": "/(.*)", "dest": "app/main.py"}]
}
```

### Docker

```bash
docker build -t mentorgold-api .
docker run -p 3001:3001 mentorgold-api
```

## License

MIT
