"""What the app may report, and nothing else.

The same idea as Sauté's usage table, for the same three reasons:

  - **The privacy promise stays checkable.** Everything this server accepts is on
    this screen. The App Store privacy declaration and the policy are written
    from it, not from memory.
  - **Nothing anybody typed or picked can land here.** No file name, no ringtone
    name, no contact, no text. Details are small closed values or whole numbers.
    That is a property of the shape, not a rule someone has to keep obeying.
  - **A future app version cannot quietly widen it.** An unknown event is counted
    and dropped, an unknown detail is dropped. Measuring something new is a
    deliberate change on both sides.
"""

import json
import os
import re
import time
import urllib.request
from typing import Any

YES_NO = ("yes", "no")
# Premium plans: weekly, yearly and lifetime since 1.3; monthly sold until 1.2.3.
PLANS = ("weekly", "yearly", "lifetime", "monthly")
# What opened the offer (`paywall_shown`, and since 1.4 the purchase it led to).
REASONS = ("general", "sound", "bestPart", "speed", "reverb", "loop", "color", "style", "onboarding",
           "welcomeSkipped")
# The app's screens (Analytics.Screen). The console reads these values: never rename one.
SCREENS = ("onboarding", "home", "library", "settings", "sounds", "editor", "best_part", "ad_gate",
           "limit", "ready", "install", "paywall", "contact", "privacy")
# The app's built-in sounds (BuiltInSound). Never rename one: the console reads them.
SOUNDS = ("ukulele", "good-mood", "menu", "hardstyle", "vibraphone", "bells", "rhodes", "crystals",
          "wah-wah", "baroque", "dawn", "chimes", "music-box", "pixel", "neon", "turkish-march",
          "fur-elise", "ode-to-joy", "little-night-music", "canon")


class KnownSounds:
    """A sound's id: one of the app's own, or one of the online catalogue (the
    sound of the day, 1.3), whose list this server reads from where the app reads
    it, at most once an hour. Still a closed list: an id the catalogue does not
    name is dropped, and the catalogue's own ids are checked to be plain slugs."""

    URL = os.environ.get("CATALOGUE_URL", "https://sons.phoneringtones.fr/catalogue.json")
    SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

    def __init__(self, builtin: tuple[str, ...]):
        self.builtin = frozenset(builtin)
        self.catalogue: frozenset[str] = frozenset()
        self.fetched = 0.0

    def load(self, ids) -> None:
        self.catalogue = frozenset(i for i in ids if isinstance(i, str) and self.SLUG.match(i))
        self.fetched = time.time()

    def _refresh(self) -> None:
        if not self.URL or time.time() - self.fetched < 3600:
            return
        self.fetched = time.time()  # a failure waits an hour too: never a fetch per event
        try:
            # Named: Cloudflare turns away Python's default user agent (403).
            request = urllib.request.Request(self.URL, headers={"User-Agent": "phone-ringtones-stats"})
            with urllib.request.urlopen(request, timeout=3) as r:
                self.load(s.get("id") for s in json.load(r).get("sounds", []))
        except Exception:
            pass

    def __contains__(self, value: object) -> bool:
        if value in self.builtin:
            return True
        self._refresh()
        return value in self.catalogue


SOUND = KnownSounds(SOUNDS)

# Each event and the details it may carry. A tuple means "one of these strings",
# `int` a whole number between 0 and 10 000. Anything else is dropped.
EVENTS: dict[str, dict[str, Any]] = {
    # A sitting begins. What "who comes back" is computed from.
    "app_opened": {"cold": YES_NO, "style": ("classic", "pop")},
    # The product's one moment of value.
    # Every setting since 1.2.3: fades in whole seconds (0 = none, 0 under the
    # loop), volume in %, bass, levelling, the built-in sound, length in seconds.
    "ringtone_created": {"best_part": YES_NO, "enhance": YES_NO, "premium": YES_NO,
                         "speed": ("normal", "sped_up", "slowed", "nightcore"), "loop": YES_NO,
                         # 1.4: slowed + reverb and its intensity.
                         "reverb": ("off", "light", "medium", "strong"),
                         "source": ("video", "audio", "sound"),
                         "fade_in": int, "fade_out": int, "volume": int,
                         "bass": ("off", "light", "strong"), "optimized": YES_NO,
                         "sound": SOUND, "duration": int,
                         # 1.3: a sound set straight from its bell, without the editor.
                         "quick": YES_NO},
    "ringtone_installed": {},
    # 1.4: whether the ad had been loaded ahead, while the person edited.
    "ad_watched": {"preloaded": YES_NO},
    "paywall_shown": {"reason": REASONS},
    # The welcome of 1.3: the screen reached, and the effects tried on the demo sound.
    "onboarding_step": {"step": ("promise", "result", "effects", "locks")},
    "onboarding_effect": {"effect": ("sped_up", "loop", "bass", "enhance", "fade", "volume", "slowed_reverb",
                                     "nightcore")},
    "purchase": {"product": PLANS, "trial": YES_NO, "reason": REASONS},
    # The way to a purchase (1.3): a plan touched, the advantages unfolded, the
    # purchase asked of Apple and how it ended, how the offer was left.
    "paywall_plan_selected": {"plan": PLANS},
    "paywall_expanded": {},
    "purchase_started": {"plan": PLANS, "trial": YES_NO, "reason": REASONS},
    "purchase_ended": {"plan": PLANS, "result": ("success", "cancelled", "pending", "failed", "unverified",
                                                  "unavailable")},
    "paywall_dismissed": {"how": ("close", "free", "purchased")},
    "auto_best_part_opened": {"allowed": YES_NO},
    "auto_best_part_started": {},
    "auto_best_part_completed": {"result": ("found_1", "found_2", "found_3", "too_short", "silent",
                                            "nothing_relevant")},
    "auto_best_part_option_selected": {"recommended": YES_NO},
    "smart_audio_enhance_opened": {"allowed": YES_NO},
    "smart_audio_enhance_applied": {},
    "smart_audio_enhance_undone": {},
    "smart_audio_enhance_compared": {"heard": ("original", "enhanced")},
    # A free user heard the five-second sample of a speed, or (1.4) of slowed +
    # reverb at one intensity. Read against "purchase".
    "speed_sampled": {"speed": ("sped_up", "slowed", "nightcore", "reverb_light", "reverb_medium",
                                "reverb_strong")},
    "app_color_chosen": {"kind": ("auto", "free", "premium")},
    # Classique or Pop (Premium, 1.2), chosen in Settings.
    "app_style_chosen": {"style": ("classic", "pop")},
    # A built-in sound starred or unstarred (1.2): which sounds people keep.
    "sound_favorited": {"sound": SOUND, "on": YES_NO},
    # 1.3: a sound listened to for 3 s or more (or to its end), like a stream.
    # Hidden from people for now; the console counts them per sound.
    "sound_played": {"sound": SOUND, "from": ("home", "gallery", "popular")},
    "contact_assignment_started": {"installed": YES_NO},
    "contact_permission_granted": {},
    "contact_permission_denied": {},
    "contact_selected": {"installed": YES_NO},
    # From the contact screen (1.2.3): install then open the card (share), or,
    # before iOS 26, the GarageBand guide (garageband).
    "contact_install_tapped": {"how": ("share", "garageband")},
    # The contact's card was opened. iOS never says whether the ringtone was then chosen.
    "contact_assignment_completed": {},
    # A screen came into view (1.3): which screens people reach. No time spent.
    "screen_viewed": {"screen": SCREENS},
    # The last sitting ended in front without going to the background: a crash, a
    # freeze iOS killed, or memory running out. Sent on the next launch, with the
    # screen it happened on. No stack, no message.
    "crash_reported": {"screen": SCREENS},
    # 1.4: a creation blocked because no ad came (Google had none, or no
    # network), or the ad closed before its end. What an eCPM floor would cost.
    "ad_unavailable": {"why": ("no_fill", "offline", "interrupted")},
    # 1.4: the ad gate closed without the ad, and where it was then.
    "ad_gate_left": {"at": ("offer", "loading", "watching", "no_fill", "offline", "interrupted"),
                     "how": ("closed", "premium")},
    # 1.4: the band under the doors opened the app's TikTok account.
    "tiktok_opened": {"from": ("doors",)},
    # 1.4, the weekly notification: the home card's answer, iOS's answer, a
    # notification touched, the switch in Settings.
    "notification_offered": {"answer": YES_NO},
    "notification_permission": {"granted": YES_NO},
    "notification_opened": {},
    "notification_switched": {"on": YES_NO},
    # 1.4: Settings › "J'ai un code" opened Apple's sheet for offer codes.
    "offer_code_opened": {},
}

MAX_EVENTS_PER_BATCH = 200


def clean(name: str, props: object) -> dict[str, Any] | None:
    """The event's details reduced to what the vocabulary admits, or None if unknown."""
    allowed = EVENTS.get(name)
    if allowed is None:
        return None
    if not isinstance(props, dict):
        return {}
    kept: dict[str, Any] = {}
    for key, rule in allowed.items():
        value = props.get(key)
        # The app's queue on disk holds text: a whole number may come as "3".
        if rule is int and isinstance(value, str) and value.isdigit() and len(value) <= 5:
            value = int(value)
        if isinstance(rule, (tuple, KnownSounds)) and isinstance(value, str) and value in rule or rule is int and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000:
            kept[key] = value
    return kept
