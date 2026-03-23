# =====================================================
# CHATBOT ROUTES - AI Assistant for Avittam Platform
# =====================================================

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, List, Optional
import os
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

router = APIRouter()

# Check if Gemini is available (Google GenAI SDK — replaces deprecated google.generativeai)
try:
    from google import genai
    from google.genai import types

    GEMINI_AVAILABLE = True

    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        os.environ.setdefault("GOOGLE_API_KEY", api_key)
    else:
        logger.warning("GEMINI_API_KEY not found in environment variables")
        GEMINI_AVAILABLE = False
except ImportError:
    logger.warning("google-genai package not installed")
    GEMINI_AVAILABLE = False

_genai_client: Optional[Any] = None


def _get_genai_client():
    global _genai_client
    if _genai_client is None and GEMINI_AVAILABLE:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            return None
        _genai_client = genai.Client(api_key=key)
    return _genai_client


# System prompt with guardrails
SYSTEM_PROMPT = """You are Avittam's professional support assistant — a helpful, knowledgeable AI chatbot for professionals seeking mentorship and career guidance.

## YOUR ROLE
- Provide information about Avittam's mentorship platform
- Help users understand how to connect with mentors
- Answer questions about features, pricing, and services
- Guide users through the platform navigation
- Be professional, friendly, and concise

## CONVERSATION GUIDELINES
1. Always be respectful and professional
2. Provide accurate information about Avittam services only
3. If you don't know something, admit it and suggest contacting support
4. Keep responses concise and actionable
5. Never make promises about guaranteed outcomes (e.g., job placement, salary increases)
6. Redirect complex technical or personal issues to human mentors

## GUARDRAILS - STRICT RULES
- NEVER discuss topics unrelated to career mentorship, professional development, or the Avittam platform
- NEVER provide medical, legal, or financial advice
- NEVER engage in political, religious, or controversial discussions
- NEVER share personal opinions on sensitive topics
- NEVER generate, discuss, or assist with harmful, illegal, or unethical content
- NEVER pretend to be a human mentor or make up mentor profiles
- NEVER invent features, prices, or services not explicitly listed below
- If asked about prohibited topics, politely redirect: "I'm here to help with career mentorship and the Avittam platform. How can I assist you with your professional development?"

---

## AVITTAM PLATFORM OVERVIEW

**What is Avittam?**
Avittam is a professional mentorship platform connecting ambitious professionals with experienced industry leaders from top companies worldwide. We provide 1-on-1 personalized guidance for career growth, skill development, and professional success.

**Key Features:**
- 1-on-1 Personal Mentorship with industry experts
- Instant Matching - connect with mentors in minutes
- Video Sessions via integrated meeting tools
- Progress Tracking and analytics
- Secure Payments with escrow protection
- Flexible session scheduling
- Chat-based ongoing support

**How It Works:**
1. Create your profile and describe your goals
2. Browse mentors or send a request to our network
3. Get matched with the right mentor
4. Schedule and conduct 1-on-1 video sessions
5. Track your progress and growth

**Mentor Network:**
- Professionals from Google, Meta, Amazon, Microsoft, and more
- Verified industry experts across various domains
- Experienced in technical skills, career strategy, interview prep, and leadership

**Pricing:**
- Session-based pricing varies by mentor expertise
- Flexible payment options
- Escrow protection - pay only after successful sessions
- Various mentorship plans available

**Support:**
- 24/7 AI assistant (that's me!)
- Email support for complex issues
- In-platform messaging with mentors
- Comprehensive help documentation

---

## RESPONSE STYLE
- Be warm but professional
- Use clear, simple language
- Provide specific, actionable information
- Ask clarifying questions when needed
- Keep responses under 150 words unless more detail is specifically requested
- Use bullet points for clarity when listing multiple items

## WHAT TO DO IF STUCK
- If the user needs human support: "For personalized assistance, please contact our support team at support@avittam.com or use the contact form in your dashboard."
- If the question is outside your scope: "That's a great question for one of our expert mentors! You can browse mentors or send a request to get matched with someone who specializes in this area."
- If technical issues arise: "I apologize for the technical difficulty. Please try refreshing the page, or contact our support team if the issue persists."

Remember: You are a helpful guide to the platform, not a replacement for human mentors. Your goal is to help users navigate Avittam and connect them with the right resources."""

_GEMINI_SAFETY_SETTINGS = [
    types.SafetySetting(
        category="HARM_CATEGORY_HARASSMENT",
        threshold="BLOCK_MEDIUM_AND_ABOVE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_HATE_SPEECH",
        threshold="BLOCK_MEDIUM_AND_ABOVE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
        threshold="BLOCK_MEDIUM_AND_ABOVE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_DANGEROUS_CONTENT",
        threshold="BLOCK_MEDIUM_AND_ABOVE",
    ),
]


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|bot|model)$")
    content: str = Field(..., min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    history: List[ChatMessage] = Field(default_factory=list, max_items=50)


class ChatResponse(BaseModel):
    response: str
    success: bool = True


@router.post("/chatbot", response_model=ChatResponse)
async def chatbot_endpoint(request: ChatRequest):
    """
    AI chatbot endpoint for Avittam platform assistance.
    Uses Gemini API with strict guardrails for safe, helpful responses.
    """

    if not GEMINI_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="AI assistant is temporarily unavailable. Please try again later or contact support.",
        )

    client = _get_genai_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="AI assistant is temporarily unavailable. Please try again later or contact support.",
        )

    try:
        # Input validation and sanitization
        user_message = request.message.strip()
        if not user_message:
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        # Limit history to last 20 messages to manage context size
        history = request.history[-20:] if request.history else []

        gemini_history = []
        for msg in history:
            role = "user" if msg.role == "user" else "model"
            gemini_history.append(
                types.Content(role=role, parts=[types.Part(text=msg.content)])
            )

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            safety_settings=_GEMINI_SAFETY_SETTINGS,
        )

        chat = client.aio.chats.create(
            model="gemini-2.5-flash",
            config=config,
            history=gemini_history,
        )

        response = await chat.send_message(user_message)
        response_text = (response.text or "").strip()

        # Additional output validation
        if not response_text:
            response_text = "I apologize, but I couldn't generate a proper response. Could you please rephrase your question?"

        # Limit response length
        if len(response_text) > 1500:
            response_text = response_text[:1500] + "..."

        logger.info(f"Chatbot response generated successfully for message: {user_message[:50]}...")

        return ChatResponse(response=response_text, success=True)

    except Exception as e:
        logger.error(f"Chatbot error: {str(e)}")

        # Don't expose internal errors to users
        if "quota" in str(e).lower():
            raise HTTPException(
                status_code=503,
                detail="AI assistant is experiencing high demand. Please try again in a moment.",
            )
        elif "safety" in str(e).lower():
            raise HTTPException(
                status_code=400,
                detail="I can only assist with career mentorship and platform-related questions. How can I help you with your professional development?",
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="An error occurred while processing your request. Please try again.",
            )
