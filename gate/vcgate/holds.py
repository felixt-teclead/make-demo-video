"""Q-51: align the holds logged during filming with the spec's hold list of a clip.

The runner logs a clip's holds in spec order (landing, action holds, reading pauses, the step's own hold). A hold can
be missing (never logged, or logged outside the clip), so the lists are aligned, not zipped: a hold matches only a
spec hold of the same kind, and the alignment with the least cost wins (a length difference costs up to MATCH_CAP, a
skipped hold SKIP). A same-kind hold with the wrong length is therefore reported as off, not as missing.
"""
MATCH_CAP = 1.0
SKIP = 1.5


def _sec(h):
    try:
        return float(h.get("seconds"))
    except (TypeError, ValueError):
        return None


def align(spec, logged):
    """spec, logged: lists of {kind, seconds}. Returns (pairs [(i_spec, j_logged)], missing [i_spec], extra [j])."""
    n, m = len(spec), len(logged)
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c == inf:
                continue
            if i < n and c + SKIP < cost[i + 1][j]:
                cost[i + 1][j], back[i + 1][j] = c + SKIP, (i, j, "miss")
            if j < m and c + SKIP < cost[i][j + 1]:
                cost[i][j + 1], back[i][j + 1] = c + SKIP, (i, j, "extra")
            if i < n and j < m and (spec[i].get("kind") or "step") == (logged[j].get("kind") or "step"):
                a, b = _sec(spec[i]), _sec(logged[j])
                d = MATCH_CAP if a is None or b is None else min(MATCH_CAP, abs(a - b))
                if c + d < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1], back[i + 1][j + 1] = c + d, (i, j, "pair")
    pairs, missing, extra = [], [], []
    i, j = n, m
    while (i, j) != (0, 0):
        pi, pj, what = back[i][j]
        if what == "pair":
            pairs.append((pi, pj))
        elif what == "miss":
            missing.append(pi)
        else:
            extra.append(pj)
        i, j = pi, pj
    return pairs[::-1], missing[::-1], extra[::-1]
