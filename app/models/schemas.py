# =====================================================
# PYDANTIC SCHEMAS / MODELS
# Type definitions for request/response validation
# =====================================================

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from enum import Enum


# =====================================================
# ENUMS
# =====================================================

class UserRole(str, Enum):
    GUEST = "guest"
    MENTEE = "mentee"
    MENTOR = "mentor"
    ADMIN = "admin"


class SessionStatus(str, Enum):
    SCHEDULED = "scheduled"
    ONGOING = "ongoing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class MentorshipType(str, Enum):
    ONE_TIME = "one_time"
    LONG_TERM = "long_term"


class RequestStatus(str, Enum):
    PENDING = "pending"
    LOCKED = "locked"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class NotificationType(str, Enum):
    REQUEST = "request"
    SESSION = "session"
    PAYMENT = "payment"
    SYSTEM = "system"
    CHAT = "chat"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class OnboardingStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"


# =====================================================
# USER & AUTH SCHEMAS
# =====================================================

class UserBase(BaseModel):
    email: EmailStr
    name: str
    role: UserRole = UserRole.MENTEE
    avatar_url: Optional[str] = None


class User(UserBase):
    id: str
    is_verified: bool = False
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserProfile(BaseModel):
    id: str
    user_id: str
    bio: Optional[str] = None
    phone: Optional[str] = None
    timezone: str = "Asia/Kolkata"
    # Mentee fields
    career_goal: Optional[str] = None
    job_role: Optional[str] = None
    industry: Optional[str] = None
    experience_years: Optional[int] = None
    topics_of_interest: Optional[List[str]] = None
    availability: Optional[Dict[str, Any]] = None
    # Mentor fields
    expertise: Optional[List[str]] = None
    hourly_rate: Optional[float] = None
    total_experience_years: Optional[int] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    headline: Optional[str] = None
    # Onboarding
    onboarding_status: OnboardingStatus = OnboardingStatus.PENDING
    onboarding_completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TokenPayload(BaseModel):
    sub: str
    email: str
    role: UserRole
    exp: int


# =====================================================
# SESSION SCHEMAS
# =====================================================

class SessionBase(BaseModel):
    mentor_id: str
    mentee_id: str
    scheduled_at: datetime
    duration_minutes: int = 60


class CreateSessionRequest(BaseModel):
    mentor_id: str
    mentee_id: str
    request_id: Optional[str] = None
    scheduled_at: datetime
    duration_minutes: int = Field(default=60, ge=15, le=180)
    title: Optional[str] = None
    description: Optional[str] = None


class Session(SessionBase):
    id: str
    request_id: Optional[str] = None
    long_term_mentorship_id: Optional[str] = None
    meeting_url: Optional[str] = None
    google_meet_id: Optional[str] = None
    google_calendar_event_id: Optional[str] = None
    status: SessionStatus = SessionStatus.SCHEDULED
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    mentor_notes: Optional[str] = None
    mentee_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SessionWithUsers(Session):
    mentor: Optional[Dict[str, Any]] = None
    mentee: Optional[Dict[str, Any]] = None


class UpdateSessionStatus(BaseModel):
    status: SessionStatus


class RescheduleSession(BaseModel):
    new_time: datetime


class AddSessionNotes(BaseModel):
    notes: str


class SessionFilters(BaseModel):
    status: Optional[List[SessionStatus]] = None
    role: Literal["mentor", "mentee", "both"] = "both"
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)


# =====================================================
# MENTOR REQUEST (PING) SCHEMAS
# =====================================================

class CreateMentorRequestInput(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    topic: str = Field(..., min_length=2, max_length=100)
    mentorship_type: MentorshipType
    plan_id: Optional[str] = None
    bounty: Optional[float] = Field(default=None, ge=0)
    preferred_date: Optional[datetime] = None
    duration_minutes: int = Field(default=60, ge=15, le=180)


class BroadcastPingInput(CreateMentorRequestInput):
    target_mentors: Optional[List[str]] = None
    expertise_filter: Optional[List[str]] = None


class MentorRequest(BaseModel):
    id: str
    mentee_id: str
    title: str
    description: Optional[str] = None
    topic: str
    mentorship_type: MentorshipType
    plan_id: Optional[str] = None
    bounty: Optional[float] = None
    preferred_date: Optional[datetime] = None
    duration_minutes: int = 60
    status: RequestStatus = RequestStatus.PENDING
    locked_by: Optional[str] = None
    locked_at: Optional[datetime] = None
    lock_expires_at: Optional[datetime] = None
    accepted_by: Optional[str] = None
    accepted_at: Optional[datetime] = None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MentorRequestWithMentee(MentorRequest):
    mentee: Optional[Dict[str, Any]] = None


class RequestFilters(BaseModel):
    topic: Optional[str] = None
    mentorship_type: Optional[MentorshipType] = None
    min_bounty: Optional[float] = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)


# =====================================================
# NOTIFICATION SCHEMAS
# =====================================================

class CreateNotificationInput(BaseModel):
    user_id: str
    type: NotificationType
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=1000)
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[str] = None
    action_url: Optional[str] = None


class Notification(BaseModel):
    id: str
    user_id: str
    type: NotificationType
    title: str
    message: str
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[str] = None
    is_read: bool = False
    read_at: Optional[datetime] = None
    action_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationFilters(BaseModel):
    unread_only: bool = False
    type: Optional[NotificationType] = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)


class SendSystemNotification(BaseModel):
    title: str
    message: str
    target_role: Literal["mentee", "mentor", "all"] = "all"


# =====================================================
# PAYMENT SCHEMAS
# =====================================================

class Payment(BaseModel):
    id: str
    user_id: str
    session_id: Optional[str] = None
    long_term_mentorship_id: Optional[str] = None
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
    amount: float
    currency: str = "INR"
    status: PaymentStatus = PaymentStatus.PENDING
    paid_at: Optional[datetime] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CreatePaymentOrder(BaseModel):
    session_id: Optional[str] = None
    long_term_mentorship_id: Optional[str] = None
    amount: float = Field(..., gt=0)
    currency: str = "INR"
    description: Optional[str] = None


class VerifyPayment(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class CreateRegistrationOrder(BaseModel):
    """Mentee registration fee order (unauthenticated)"""
    email: str
    name: str  # Full name, will be split into first/last
    amount: int  # Amount in paise


# =====================================================
# GOOGLE CALENDAR / MEET SCHEMAS
# =====================================================

class CalendarEventInput(BaseModel):
    summary: str
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    attendees: List[Dict[str, str]]  # [{"email": "...", "name": "..."}]
    timezone: str = "Asia/Kolkata"


class GoogleMeetDetails(BaseModel):
    meeting_url: str
    meeting_id: str
    calendar_event_id: str
    conference_data: Dict[str, Any]


class GoogleTokens(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    expiry_date: Optional[int] = None
    token_type: str = "Bearer"
    scope: str


# =====================================================
# API RESPONSE SCHEMAS
# =====================================================

class ApiResponse(BaseModel):
    success: bool = True
    data: Optional[Any] = None
    error: Optional[str] = None
    message: Optional[str] = None


class PaginationInfo(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int


class PaginatedResponse(ApiResponse):
    pagination: Optional[PaginationInfo] = None


# =====================================================
# MENTOR DISCOVERY SCHEMAS
# =====================================================

class MentorFilters(BaseModel):
    expertise: Optional[List[str]] = None
    min_rating: Optional[float] = Field(default=None, ge=0, le=5)
    max_hourly_rate: Optional[float] = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)


class MentorPublicProfile(BaseModel):
    id: str
    name: str
    email: EmailStr
    avatar_url: Optional[str] = None
    headline: Optional[str] = None
    bio: Optional[str] = None
    expertise: Optional[List[str]] = None
    hourly_rate: Optional[float] = None
    total_experience_years: Optional[int] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    rating: Optional[float] = None
    total_sessions: int = 0

    class Config:
        from_attributes = True


# =====================================================
# WALLET & AVITTAM COINS ENUMS
# =====================================================

class WalletType(str, Enum):
    STUDENT = "student"
    MENTORSHIP = "mentorship"
    REFERRAL = "referral"


class WalletTxType(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class WalletTxCategory(str, Enum):
    COIN_LOAD = "coin_load"
    SESSION_PAYMENT = "session_payment"
    SESSION_EARNING = "session_earning"
    PLATFORM_FEE = "platform_fee"
    REFERRAL_COMMISSION = "referral_commission"
    REGISTRATION_FEE = "registration_fee"
    WITHDRAWAL = "withdrawal"
    REFUND = "refund"
    BONUS = "bonus"


class NPSBand(str, Enum):
    PROMOTER = "promoter"
    PASSIVE = "passive"
    DETRACTOR = "detractor"


# =====================================================
# WALLET SCHEMAS
# =====================================================

class Wallet(BaseModel):
    id: str
    user_id: str
    type: WalletType
    balance: float = 0
    total_credited: float = 0
    total_debited: float = 0
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WalletTransaction(BaseModel):
    id: str
    wallet_id: str
    tx_type: WalletTxType
    category: WalletTxCategory
    amount: float
    balance_after: float
    session_id: Optional[str] = None
    payment_id: Optional[str] = None
    related_user_id: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class WalletSummary(BaseModel):
    wallet: Wallet
    recent_transactions: List[WalletTransaction] = []


class MentorWalletOverview(BaseModel):
    mentorship_wallet: Optional[Wallet] = None
    referral_wallet: Optional[Wallet] = None
    total_earnings: float = 0
    total_referral_earnings: float = 0


class StudentWalletOverview(BaseModel):
    wallet: Optional[Wallet] = None
    coin_balance: float = 0
    total_loaded: float = 0
    total_spent: float = 0


# =====================================================
# COIN LOADING SCHEMAS
# =====================================================

class LoadCoinsRequest(BaseModel):
    amount_inr: float = Field(..., gt=0, description="Amount in INR to load as Avittam Coins (1 INR = 1 Coin)")


class LoadCoinsVerify(BaseModel):
    order_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# =====================================================
# SESSION COIN PAYMENT SCHEMAS
# =====================================================

class PayWithCoinsRequest(BaseModel):
    session_id: str
    mentor_id: str
    total_coins: float = Field(..., gt=0, description="Avittam Coins to pay")


class SessionCoinPayment(BaseModel):
    id: str
    session_id: str
    mentee_id: str
    mentor_id: str
    total_coins: float
    platform_fee_coins: float
    mentor_earning_coins: float
    nps_rating_id: Optional[str] = None
    platform_fee_pct: Optional[float] = None
    is_settled: bool = False
    settled_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# =====================================================
# NPS RATING SCHEMAS
# =====================================================

class SubmitNPSRating(BaseModel):
    session_id: str
    score: int = Field(..., ge=0, le=10, description="NPS score 0-10")
    feedback: Optional[str] = None


class NPSRating(BaseModel):
    id: str
    session_id: str
    rater_id: str
    rated_mentor_id: str
    score: int
    band: NPSBand
    platform_fee_pct: float
    feedback: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NPSSettlementResult(BaseModel):
    nps_score: int
    nps_band: str
    platform_fee_pct: float
    total_coins: float
    platform_fee_coins: float
    mentor_earning_coins: float
    mentor_name: Optional[str] = None


# =====================================================
# MENTOR REGISTRATION FEE SCHEMAS
# =====================================================

class MentorRegistrationFeeRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Registration fee amount in INR")
    referral_code: Optional[str] = None


class MentorRegistrationFeeVerify(BaseModel):
    fee_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class MentorRegistrationFee(BaseModel):
    id: str
    mentor_id: str
    amount: float
    referred_by_id: Optional[str] = None
    referral_code: Optional[str] = None
    referral_commission: float = 0
    platform_share: float = 0
    is_paid: bool = False
    paid_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# =====================================================
# WITHDRAWAL SCHEMAS
# =====================================================

class WithdrawalRequest(BaseModel):
    wallet_type: WalletType
    amount: float = Field(..., gt=0, description="Amount in Avittam Coins to withdraw")
