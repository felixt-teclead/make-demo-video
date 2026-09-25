"""Control matching and the never-offer list: the jev wrapper's own module (`vcjev.offer`, M2).

The validator must judge a control exactly the way the runner will at take time, so there is one implementation
(`jev/vcjev/offer.py`, or `$VC_JEV_PATH`) and no copy here.
"""
import os
import re
import sys

from .profiles import ROOT

_jev = os.environ.get("VC_JEV_PATH") or os.path.join(ROOT, "jev")
if os.path.isdir(os.path.join(_jev, "vcjev")) and _jev not in sys.path:
    sys.path.insert(0, _jev)
from vcjev.offer import DenyList, ResolveError, offered_set  # noqa: E402,F401

SOURCE = "vcjev.offer"


# Write-looking words (language data, C-40 allows it in the core; D-13 extends it in S2). Matched at a word start
# on control labels and typed text; a hit is a spec error unless the step is a flagged, approved write (F-10).
WRITE_WORDS = (r"speicher\w*", r"save\w*", r"erstell\w*", r"create\w*", r"anleg\w*", r"hinzufüg\w*", r"add\b",
               r"neue[rs]?\b", r"new\b", r"absend\w*", r"submit\w*", r"send\b", r"bearbeit\w*", r"edit\b",
               r"einfrier\w*", r"freeze\w*", r"veröffentlich\w*", r"publish\w*", r"hochlad\w*", r"upload\w*",
               r"import\w*", r"bestätig\w*", r"confirm\w*", r"übernehm\w*", r"apply\b", r"einreich\w*")
_WRITE_RE = re.compile(r"(?<!\w)(" + "|".join(WRITE_WORDS) + r")", re.I)


def looks_like_write(text):
    m = _WRITE_RE.search(text or "")
    return m.group(0) if m else None
