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

from typing import Any

YES_NO = ("yes", "no")

# Each event and the details it may carry. A tuple means "one of these strings",
# `int` a whole number between 0 and 10 000. Anything else is dropped.
EVENTS: dict[str, dict[str, Any]] = {
    # A sitting begins. What "who comes back" is computed from.
    "app_opened": {"cold": YES_NO},
    # The product's one moment of value.
    "ringtone_created": {"best_part": YES_NO, "enhance": YES_NO, "premium": YES_NO,
                         "speed": ("normal", "sped_up", "slowed"), "loop": YES_NO,
                         "source": ("video", "audio", "sound")},
    "ringtone_installed": {},
    "ad_watched": {},
    "paywall_shown": {"reason": ("general", "sound", "bestPart", "speed", "loop", "color")},
    "purchase": {"product": ("lifetime", "monthly")},
    "auto_best_part_opened": {"allowed": YES_NO},
    "auto_best_part_started": {},
    "auto_best_part_completed": {"result": ("found_1", "found_2", "found_3", "too_short", "silent",
                                            "nothing_relevant")},
    "auto_best_part_option_selected": {"recommended": YES_NO},
    "smart_audio_enhance_opened": {"allowed": YES_NO},
    "smart_audio_enhance_applied": {},
    "smart_audio_enhance_undone": {},
    "smart_audio_enhance_compared": {"heard": ("original", "enhanced")},
    # A free user heard the five-second sample of a speed: the one Premium effect
    # that can be heard before paying. Read against "purchase".
    "speed_sampled": {"speed": ("sped_up", "slowed")},
    "app_color_chosen": {"kind": ("auto", "free", "premium")},
    "contact_assignment_started": {"installed": YES_NO},
    "contact_permission_granted": {},
    "contact_permission_denied": {},
    "contact_selected": {},
    # The contact's card was opened. iOS never says whether the ringtone was then chosen.
    "contact_assignment_completed": {},
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
        if isinstance(rule, tuple) and isinstance(value, str) and value in rule or rule is int and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000:
            kept[key] = value
    return kept
