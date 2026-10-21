"""Social route registration and compatibility exports.

Domain handlers live in separate modules; shared presentation and graph queries
are services. Each mutation still commits before publishing its queued events.
"""

from fastapi import APIRouter
from app.routers.social_profiles import (
    search_users as search_users,
    user_suggestions as user_suggestions,
    get_public_profile as get_public_profile,
    weekly_leaderboard as weekly_leaderboard,
    find_user as find_user,
)
from app.routers.social_profiles import router as social_profiles_router
from app.routers.social_friendships import (
    send_invite as send_invite,
    list_invites as list_invites,
    cancel_invite as cancel_invite,
    respond_invite as respond_invite,
    list_friends as list_friends,
    remove_friend as remove_friend,
)
from app.routers.social_friendships import router as social_friendships_router
from app.routers.social_feed import (
    CatPostCreate as CatPostCreate,
    CommentCreate as CommentCreate,
    OFFICIAL_USER_ID as OFFICIAL_USER_ID,
    community_reports as community_reports,
    community_report as community_report,
    gallery_photo as gallery_photo,
    repost_report as repost_report,
    publish_report as publish_report,
    _share_report as _share_report,
    feed as feed,
    upload_post_photo as upload_post_photo,
    get_post_photo as get_post_photo,
    create_post as create_post,
    toggle_like as toggle_like,
    add_comment as add_comment,
)
from app.routers.social_feed import router as social_feed_router
from app.routers.social_devices import register_device_token as register_device_token
from app.routers.social_devices import router as social_devices_router
from app.routers.social_chat import (
    conversations_list as conversations_list,
    conversation_details as conversation_details,
    messages as messages,
    send_message as send_message,
    upload_chat_photo as upload_chat_photo,
    get_chat_photo as get_chat_photo,
    mark_conversation_read as mark_conversation_read,
    toggle_message_reaction as toggle_message_reaction,
    edit_message as edit_message,
    delete_message as delete_message,
    user_presence as user_presence,
)
from app.routers.social_chat import router as social_chat_router
from app.services.social_presenter import (
    _user_avatar_url as _user_avatar_url,
    _public_user_response as _public_user_response,
    _habitat_preview as _habitat_preview,
)
from app.services.social_graph import (
    _mutual_friends_count as _mutual_friends_count,
    _get_friendship_status as _get_friendship_status,
)
from app.services.chat_presenter import (
    _build_message_response as _build_message_response,
)
from app.dependencies import current_user as current_user
from app.schemas import FeedItemResponse as FeedItemResponse
from app.services.storage_service import photo_storage as photo_storage

router = APIRouter()
router.include_router(social_profiles_router)
router.include_router(social_friendships_router)
router.include_router(social_feed_router)
router.include_router(social_devices_router)
router.include_router(social_chat_router)
