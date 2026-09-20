"""Web Push notifications - the actual delivery mechanism behind the
Notifications page's toggle, so a reminder/azan alert reaches a phone even
when its browser tab isn't open (unlike the old Notification.requestPermission()-
only approach, which only ever fired while the page itself was active).

One VAPID keypair identifies this server to push services (Google's FCM,
Apple's web push, Mozilla's autopush, etc.) - generated once per install via
vapid_private.pem; the public half is derived from it at import time, never
stored separately. Subscriptions (one per browser/device that opted in) are
kept in a flat JSON file rather than config.json, since this is
runtime-managed state a user never edits directly, same reasoning as
quran_state.json/history.jsonl.
"""
import json
import os

from py_vapid import Vapid02
from pywebpush import webpush, WebPushException

_HERE = os.path.dirname(os.path.abspath(__file__))
VAPID_KEY_FILE = os.path.join(_HERE, "vapid_private.pem")
SUBSCRIPTIONS_FILE = os.path.join(_HERE, "push_subscriptions.json")
VAPID_CLAIM_EMAIL = "mailto:sharjeel.kiyani@ssmarttec.com"

_vapid = None
_public_key_b64 = None


def _ensure_vapid():
    """Generates a keypair on first run (a fresh install), otherwise loads
    the existing one - regenerating on every restart would invalidate every
    saved subscription, since the public key is what a browser used to
    create its subscription in the first place.

    Vapid02.from_file() is a classmethod that *returns* a new populated
    instance rather than mutating one in place - and it already generates
    and saves a fresh key itself if the file doesn't exist yet, so there's
    no separate "does it exist" branch needed here.
    """
    global _vapid, _public_key_b64
    if _vapid is not None:
        return
    _vapid = Vapid02.from_file(VAPID_KEY_FILE)
    os.chmod(VAPID_KEY_FILE, 0o600)
    # .public_key is only set as a side effect of generate_keys() - deriving
    # it from the private key directly works regardless of which path
    # from_file() took (loading vs. generating).
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    _public_key_b64 = _vapid.private_key.public_key().public_bytes(
        encoding=Encoding.X962,
        format=PublicFormat.UncompressedPoint,
    )


def get_public_key_b64url():
    import base64
    _ensure_vapid()
    return base64.urlsafe_b64encode(_public_key_b64).decode("ascii").rstrip("=")


def _load_subscriptions():
    try:
        with open(SUBSCRIPTIONS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_subscriptions(subs):
    tmp = SUBSCRIPTIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(subs, f, indent=2)
    os.replace(tmp, SUBSCRIPTIONS_FILE)


def add_subscription(sub_info):
    subs = _load_subscriptions()
    endpoint = sub_info.get("endpoint")
    if not any(s.get("endpoint") == endpoint for s in subs):
        subs.append(sub_info)
        _save_subscriptions(subs)


def remove_subscription(endpoint):
    subs = _load_subscriptions()
    new_subs = [s for s in subs if s.get("endpoint") != endpoint]
    if len(new_subs) != len(subs):
        _save_subscriptions(new_subs)


def send_to_all(title, body, tag=None, play_url=None):
    """Best-effort, never raises - one subscriber's expired/broken
    subscription (phone factory-reset, notifications revoked, etc.) must
    never stop the reminder from reaching everyone else. A 404/410 from the
    push service specifically means "this subscription is gone for good",
    so those are pruned; any other error is just logged and left alone,
    since it might be transient (the push service being briefly down).

    play_url, when given, is opened by the service worker's
    notificationclick handler instead of the app's home page - a push event
    can't play audio itself (no user gesture), so this is what lets tapping
    an azan notification actually play the azan on a phone that's away from
    the Pi's own speakers."""
    _ensure_vapid()
    subs = _load_subscriptions()
    if not subs:
        return
    payload = json.dumps({"title": title, "body": body, "tag": tag or "smart-azan", "play_url": play_url})
    still_valid = []
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_KEY_FILE,
                vapid_claims={"sub": VAPID_CLAIM_EMAIL},
            )
            still_valid.append(sub)
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                print(f"[Push] subscription gone (HTTP {status}), removing")
            else:
                print(f"[Push] send failed: {e}")
                still_valid.append(sub)
        except Exception as e:
            print(f"[Push] send failed: {e}")
            still_valid.append(sub)
    if len(still_valid) != len(subs):
        _save_subscriptions(still_valid)
