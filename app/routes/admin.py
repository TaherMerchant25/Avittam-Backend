from typing import List, Dict, Any

from fastapi import APIRouter, Depends, Query, Body

from app.middleware.auth import require_admin
from app.models.schemas import User, ApiResponse
from app.services import admin as admin_service


router = APIRouter()


@router.get("/mentor-milestones", response_model=ApiResponse)
async def get_mentor_milestones(
    threshold: float = Query(10_000, ge=0),
    user: User = Depends(require_admin),
) -> ApiResponse:
    """
    List mentors whose **combined** wallet earnings (mentorship + referral
    total_credited) are at or above the given threshold.

    Each mentor record includes:
    - `mentorship_earnings` — total credited to the mentorship wallet
    - `referral_earnings`   — total credited to the referral wallet
    - `total_earnings`      — combined total (used for threshold comparison)
    """
    mentors: List[Dict[str, Any]] = await admin_service.get_mentor_milestones(threshold)

    return ApiResponse(
        success=True,
        data={
            "threshold": threshold,
            "count": len(mentors),
            "mentors": mentors,
        },
        message="Mentor earning milestones fetched successfully",
    )


@router.post("/check-milestones", response_model=ApiResponse)
async def check_milestones(
    threshold: float = Query(10_000, ge=0),
    user: User = Depends(require_admin),
) -> ApiResponse:
    """
    Trigger a milestone check: scan all mentors, find those who crossed the
    threshold for the first time, and dispatch in-app notifications to all
    admin users.

    Safe to call repeatedly — already-notified milestones are de-duplicated
    via the `admin_milestone_notifications` table.
    """
    result = await admin_service.check_and_notify_milestones(threshold)

    return ApiResponse(
        success=True,
        data=result,
        message=(
            f"Milestone check complete — {result['newly_notified']} new notification(s) sent."
        ),
    )

