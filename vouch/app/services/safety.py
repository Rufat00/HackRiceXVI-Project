"""Chat safety: detect attempts to move payment off-platform.

'Venmo me and I'll knock $20 off' is the single most common way escrow gets
bypassed. We don't block the message (that hides evidence); we flag it and
the recipient sees a warning that off-platform deals aren't protected.
"""
import re

PAYMENT_APPS = re.compile(
    r"\b(venmo|cash\s?app|cashapp|zelle|paypal|apple\s?cash|apple\s?pay|google\s?pay|"
    r"wire|western\s?union|crypto|bitcoin|btc|eth|usdt|gift\s?card|money\s?order)\b",
    re.I,
)
HANDLE = re.compile(r"(?<![\w@])[@$][A-Za-z][A-Za-z0-9_\-]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
PRESSURE = re.compile(
    r"\b(pay (me )?(first|upfront|now|outside|directly)|off[\s-]?(the[\s-])?(app|platform)|"
    r"skip (the )?(escrow|fee|app)|deposit (first|now|to hold)|hold it for you|"
    r"cheaper if|knock .{0,12}off|send (the )?(money|payment) (first|now|directly))\b",
    re.I,
)


def inspect(body):
    """Returns (flagged: bool, reason: str|None). Cheap regex; a real build
    would add a classifier, but this catches the pattern judges will type."""
    reasons = []
    if PAYMENT_APPS.search(body):
        reasons.append("mentions an outside payment app")
    if HANDLE.search(body):
        reasons.append("contains a payment handle")
    if PHONE.search(body):
        reasons.append("contains a phone number")
    if EMAIL.search(body):
        reasons.append("contains an email address")
    if PRESSURE.search(body):
        reasons.append("pressures for off-platform or upfront payment")
    if not reasons:
        return False, None
    return True, "; ".join(reasons)
