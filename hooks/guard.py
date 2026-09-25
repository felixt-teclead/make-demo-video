#!/usr/bin/env python3
"""PreToolUse guard of the vc-v1 plugin (F-19, C-05, C-31, C-32). Python 3 stdlib only.

Reads the hook input (JSON) on stdin. Exit 0 = allow; exit 2 = block, with the reason and what to do instead on
stderr (Claude Code shows it to the model).

(a) the QA definition (gate, gate fixtures, cutter quality rules, their CLIs, these hooks, the plugin manifest and
    Claude settings) cannot be edited, moved or overwritten, unless the owner started the session with
    VC_OWNER_QA_UNLOCK=1 in the environment. Nobody may set that flag from a tool call. Approval data (the spec's
    [approval] table, specs/**/.approved/) is written only by `vc-spec approve`, flag or not. Covered writers:
    editors, redirects, mutators (also behind env/timeout/xargs), cp/mv/install -t, sed/perl/awk in place,
    archives unpacked into the checkout, curl/wget downloads, git commands that rewrite paths or the whole tree,
    and code fed to shells or interpreters by -c, heredoc, here-string or pipe.
(b) credentials (the secrets env file, /run/secrets, env files under ~/.config, .env files, the browser profile,
    cookies, key files, environment dumps, $*_KEY/$*TOKEN expansions) cannot be read, copied, printed or archived.
    The owner flag does not lift this. The secrets file is also blocked through any parent folder (home and /
    included), symlinks, globs, $VARS (plus what bin/vc-lib.sh exports), `cd`, and command substitutions or xargs
    pipelines that read the settings/config files.
(c) the fixer subagent (hook input `agent_type` ending in "fixer", e.g. "vc-v1:fixer") may run only read-only
    helpers, may edit only the spec, quirk records and the step runner, and may write only its fix note.
"""
import codecs
import fnmatch
import glob
import itertools
import json
import os
import re
import shlex
import sys

OWNER_FLAG = "VC_OWNER_QA_UNLOCK"
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

QA_GLOBS = ["gate/*", "vc/cut/*", "bin/vc-gate", "bin/vc-gate-*", "bin/vc-cut", "hooks/*", ".claude-plugin/*",
            ".claude/settings.json", ".claude/settings.local.json"]
FIXER_EDIT_GLOBS = ["specs/quirks/*", "loop/vcloop/take_runner.py", "jev/vcjev/*"]
PROFILE_NAMES = {"Cookies", "Cookies-journal", "Local State", "Login Data", "Login Data-journal", "Web Data"}
KEYFILE_RE = re.compile(r"(?i)(api[_-]?key|\.key$|\.pem$|credentials)")
SECRET_VAR_RE = re.compile(
    r"\$\{?!?[A-Za-z_]*(API_KEY|_KEY|TOKEN|SECRET|PASSWORD|PASSWD|COOKIE|ENV_FILE)[A-Za-z0-9_]*\}?")
# a variable the guard cannot resolve blocks when its name looks like config, settings, env file or a secret
SENSITIVE_NAME_RE = re.compile(r"(?i)ENV_FILE|CONFIG|SETTINGS|SECRET|KEY|TOKEN|PASSW|COOKIE|CRED|PROFILE")
# a command substitution (or xargs/read pipeline) the guard cannot resolve blocks when it touches these
CONFIG_TEXT_RE = re.compile(r"settings[\w.-]*\.env|VC_SETTINGS|ENV_FILE|\.config\b|vc-lib|/run/secrets|compose\.env|"
                            r"\bvc\.env\b|\bsecrets?\b|XDG_CONFIG")
CODE_CONFIG_RE = re.compile(r"['\"/]\.config\b")
# programs that read a whole folder (or the current one when given no path)
FOLDER_READERS = {"ls", "dir", "vdir", "tree", "find", "fd", "fdfind", "rg", "ag", "ack", "tar", "bsdtar", "zip",
                  "7z", "rsync", "du", "locate", "cpio", "pax"}
GREPS = {"grep", "egrep", "fgrep", "zgrep"}
# programs that do not read what they are given: they may name home or a folder above it (never the secrets)
NON_READERS = {"echo", "printf", "test", "[", "[[", "dirname", "basename", "realpath", "readlink", "mkdir", "true",
               ":", "which", "type"}
PROC_ENV_RE = re.compile(r"/proc/[^\s]*/environ")
CODE_ENV_RE = re.compile(r"os\.environ|process\.env|getenv\b|ENV\[")
PROFILE_TEXT_RE = re.compile(r"(^|[\s'\":=])/profile(/|\s|$|['\"])|\w-profile\b|\bCookies\b|Local State|Login Data")
MUTATORS = {"rm", "rmdir", "unlink", "truncate", "shred", "touch", "chmod", "chown", "chattr", "ln", "mv", "tee",
            "patch", "dd", "sponge"}
DEST_ONLY = {"cp", "install", "rsync"}
INPLACE = {"sed", "perl", "ruby"}
# git subcommands that change the working tree (by path, or for the whole tree at branch level)
GIT_TREE = {"checkout", "restore", "reset", "switch", "stash", "pull", "merge", "rebase", "cherry-pick", "revert",
            "am", "apply", "clean", "checkout-index", "read-tree", "rm", "mv", "bisect", "filter-branch",
            "filter-repo", "sparse-checkout"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
INTERP_RE = re.compile(r"^(python[0-9.]*|pypy[0-9.]*|node|nodejs|deno|bun|perl|ruby|php|lua[0-9.]*|Rscript|tclsh|"
                       r"irb|osascript)$")
WRAPPERS = {"env", "timeout", "nice", "ionice", "nohup", "stdbuf", "chrt", "taskset", "sudo", "doas", "command",
            "exec", "builtin", "time", "unbuffer"}
# inline code (-c, heredoc, here-string, stdin): what writes, and what names the QA definition
WRITE_CODE_RE = re.compile(r"""\bopen\w*\s*\([^)]*['"]\s*(\+?>|\+<|[rbtU]*[wax+][rbt+]*['"])|\bwrite|\bunlink|"""
                           r"\bremove\b|os\.remove|\brename|os\.replace|rmtree|shutil\.|copyfile|\bcopy\w*\(|"
                           r"\bmove\(|symlink|truncate|chmod|mkdir|makedirs|\btouch\(|os\.system|subprocess|"
                           r"popen|spawn|\bexec[lv]|\bsystem\s*\(|rmSync|rmdir|appendFile|File\.delete|sysopen|`")
PROTECTED_FRAG_RE = re.compile(r"""gate/|vc/cut|vc-gate|vc-cut|hooks/|\.claude-plugin|\.claude/settings|"""
                               r"""['"](gate|hooks)['"]""")
FIXER_BASH = {"vc-spec": {"validate", "view", "diff", "contract", "fingerprint"},
              "vc-loop": {"status", "summary", "best"},
              "git": {"diff", "status", "log", "show"}}
FIXER_PLAIN = {"ls", "cat", "head", "tail", "wc", "grep", "rg", "diff", "jq", "stat", "file", "pwd"}

MSG_QA = ("this changes the QA definition ({what}): the checks, thresholds, fixtures, cutter quality rules or these "
          "guards. Patch the spec, not the judge: fix the failing step's how (control, wait, hold, warm-up), or stop "
          "with reason 'qa' if the check itself looks wrong. Only the owner can lift this, by starting the session "
          "with VC_OWNER_QA_UNLOCK=1.")
MSG_FLAG = ("only the owner sets VC_OWNER_QA_UNLOCK, in the environment when starting the session; an agent never "
            "sets, exports or writes it. Patch the spec, not the judge, or ask the owner.")
MSG_SECRET = ("{what} is a credential (secrets env file, browser profile, cookies, API keys or the environment). "
              "Credentials never enter model context and are never copied (C-05, C-31, C-32). Refer to the env file "
              "by path only; the loop, jev and the scripted login read it themselves. For a login problem use the "
              "login stop point (the human logs in over the remote view).")
MSG_APPROVAL = ("the spec's [approval] table and specs/**/.approved/ are written only by `bin/vc-spec approve`, after "
                "the owner's OK (or the recorded unattended self-approval). Change the spec's how instead; a change "
                "of what the video shows needs a new approval (stop point 1).")
MSG_SPEC_SHELL = ("edit spec files with the Edit tool, not with shell in-place edits, redirects or git checkouts, so "
                  "the guard can check the [approval] table.")
MSG_FIXER_BASH = ("the fixer only edits; it runs only read-only helpers: vc-spec validate|view|diff|contract|"
                  "fingerprint, vc-loop status|summary|best, git diff|status|log|show, ls/cat/head/tail/wc/grep/diff/jq "
                  "(no pipes, redirects or chaining). The loop script starts every retake, dry run, container, cut "
                  "and QA run. Write your fix note and end; the orchestrator resumes the loop.")
MSG_FIXER_EDIT = ("the fixer may edit only the spec (not its [approval]), quirk records under specs/quirks/ and the "
                  "step runner (loop/vcloop/take_runner.py, jev/vcjev/). If the fix needs anything else, write a "
                  "'stop' fix note with the reason.")
MSG_FIXER_WRITE = ("the fixer creates only its fix note (<job>/fixes/fix-<n>.json, the request's 'out'); change "
                   "existing files with Edit.")


class Block(Exception):
    pass


def block(rule, msg, **kw):
    raise Block(f"vc-v1 guard blocked this ({rule}): " + msg.format(**kw))


# ------------------------------------------------------------------------------------------------ paths
def home_dir():
    return os.path.expanduser("~")


def norm(p, cwd):
    """Absolute, with ~ expanded and every symlink resolved."""
    p = os.path.expanduser(str(p).strip().strip("'\""))
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return os.path.realpath(p)


def is_vc_root(d):
    if os.path.exists(os.path.join(d, "bin", "vc-loop")):
        return True
    try:
        with open(os.path.join(d, ".claude-plugin", "plugin.json")) as f:
            return json.load(f).get("name") == "vc-v1"
    except (OSError, ValueError):
        return False


def roots_for(path):
    """The plugin root, VC_ROOT, and every vc-v1 checkout above the path. Other projects are never guarded."""
    out = [PLUGIN_ROOT]
    if os.environ.get("VC_ROOT"):
        out.append(os.path.realpath(os.environ["VC_ROOT"]))
    d = path
    while d and d != os.path.dirname(d):
        if is_vc_root(d):
            out.append(d)
        d = os.path.dirname(d)
    return out


def rels(path):
    out = []
    for r in roots_for(path):
        if path == r or path.startswith(r.rstrip("/") + "/"):
            out.append(os.path.relpath(path, r))
    return out


def match_any(path, cwd, globs):
    for rel in rels(path):
        for g in globs:
            if fnmatch.fnmatch(rel, g) or rel == g.rstrip("/*"):
                return rel
    return None


def is_qa(path, cwd):
    hit = match_any(path, cwd, QA_GLOBS)
    if hit:
        return hit
    for home in {home_dir(), os.path.realpath(home_dir())}:
        if path in (os.path.join(home, ".claude", "settings.json"),
                    os.path.join(home, ".claude", "settings.local.json")):
            return path
    return None


def is_approval_dir(path):
    return "/.approved/" in path + "/"


def _settings_files():
    files = [os.path.join(PLUGIN_ROOT, "settings.env"), os.path.join(PLUGIN_ROOT, "settings.example.env")]
    if os.environ.get("VC_SETTINGS"):
        files.insert(0, os.environ["VC_SETTINGS"])
    return files


def _read_settings(f):
    out = {}
    try:
        with open(f) as fh:
            for line in fh:
                m = re.match(r"\s*(VC_[A-Z0-9_]+)\s*=\s*(\S*)", line)
                if m:
                    out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


def _abs_setting(v):
    p = os.path.expanduser(v)
    return p if os.path.isabs(p) else os.path.join(PLUGIN_ROOT, p)


_SECRETS = None


def secret_files():
    """The secrets env file(s) and /run/secrets, each as written and with symlinks resolved."""
    global _SECRETS
    if _SECRETS is not None:
        return _SECRETS
    out = {"/run/secrets", os.path.realpath("/run/secrets")}
    vals = []
    for k in ("VC_ENV_FILE", "VC_ENV_FILE_ABS"):
        if os.environ.get(k):
            vals.append(os.environ[k])
    for f in _settings_files():
        if _read_settings(f).get("VC_ENV_FILE"):
            vals.append(_read_settings(f)["VC_ENV_FILE"])
    for v in vals:
        p = _abs_setting(v)
        out.add(os.path.normpath(p))
        out.add(os.path.realpath(p))
    _SECRETS = out
    return out


def shell_vars():
    """What a shell here would expand: the environment plus what bin/vc-lib.sh exports (vc_load)."""
    v = dict(os.environ)
    home = v.setdefault("HOME", home_dir())
    v.setdefault("XDG_CONFIG_HOME", os.path.join(home, ".config"))
    v.setdefault("VC_ROOT", PLUGIN_ROOT)
    v.setdefault("VC_REPO_ABS", v["VC_ROOT"])
    v.setdefault("VC_SETTINGS", os.path.join(v["VC_ROOT"], "settings.env"))
    for k, val in _read_settings(v["VC_SETTINGS"]).items():
        v.setdefault(k, val)
    for src, dst in (("VC_ENV_FILE", "VC_ENV_FILE_ABS"), ("VC_RUNS_DIR", "VC_RUNS_ABS"),
                     ("VC_STATE_DIR", "VC_STATE_ABS")):
        if v.get(src):
            v.setdefault(dst, _abs_setting(v[src]))
    return v


def secret_reason(path):
    """Why a path is a credential, or None."""
    base = os.path.basename(path)
    for s in secret_files():
        if path == s or path.startswith(s.rstrip("/") + "/"):
            return f"the secrets file {path}"
    for home in {home_dir(), os.path.realpath(home_dir())}:
        cfg = os.path.join(home, ".config") + "/"
        if path.startswith(cfg) and (base == "env" or base.endswith(".env")):
            return f"the env file {path}"
    if base == ".env" or base.startswith(".env."):
        return f"the env file {path}"
    parts = path.split("/")
    if path == "/profile" or path.startswith("/profile/") or any(re.search(r"\w-profile$", x) for x in parts):
        return f"the browser profile ({path})"
    if base in PROFILE_NAMES:
        return f"browser credential data ({base})"
    if KEYFILE_RE.search(base):
        return f"the key file {path}"
    return None


def secret_ancestor(path, allow_root=True):
    """A folder that holds a secrets file: home and / included (a read of it reaches the file)."""
    if path == "/" and not allow_root:
        return None
    pre = path.rstrip("/") + "/"
    for s in secret_files():
        if s.startswith(pre):
            return f"a folder holding the secrets file ({path})"
    return None


def secret_hit(path, pathlike=True, allow_root=True):
    """Why reading this absolute path would reach a credential: the file, a parent folder or (pathlike) a
    credential-looking name. Checked as written and with symlinks resolved."""
    for q in dict.fromkeys((os.path.normpath(path), os.path.realpath(path))):
        if pathlike:
            why = secret_reason(q)
        else:
            why = next((f"the secrets file {q}" for s in secret_files()
                        if q == s or q.startswith(s.rstrip("/") + "/")), None)
        why = why or secret_ancestor(q, allow_root)
        if why:
            return why
    return None


GLOB_CHARS = re.compile(r"[*?\[]")


def _seg_match(p, s):
    """Segment-wise glob match; True when the pattern names s, a parent folder of s or something inside s."""
    if not p or not s:
        return True
    if p[0] == "**":
        return _seg_match(p[1:], s) or _seg_match(p, s[1:])
    return fnmatch.fnmatchcase(s[0], p[0]) and _seg_match(p[1:], s[1:])


def glob_hit(pattern):
    """Why an absolute glob pattern reaches a credential: it matches the secrets file or one of its parent
    folders (by name, even when nothing exists yet), or one of its expansions is a credential."""
    cands = [os.path.normpath(pattern)]
    parts = cands[0].split("/")
    i = next((k for k, x in enumerate(parts) if GLOB_CHARS.search(x)), len(parts))
    prefix = "/".join(parts[:i]) or "/"
    cands.append(os.path.join(os.path.realpath(prefix), *parts[i:]))
    for c in cands:
        segs = [x for x in c.split("/") if x]
        for s in secret_files():
            if _seg_match(segs, [x for x in s.split("/") if x]):
                return f"a pattern matching the secrets file or its folder ({pattern})"
    for hit in itertools.islice(glob.iglob(cands[0]), 2000):
        why = secret_hit(hit, allow_root=False)
        if why:
            return why
    return None


def loose_hit(pattern):
    """A word built around a command substitution the guard cannot resolve: '*' may be anything, '/' included."""
    targets = set()
    for s in secret_files():
        d = s
        while d not in ("/", ""):
            targets.add(d)
            d = os.path.dirname(d)
    for t in targets:
        if fnmatch.fnmatchcase(t, pattern):
            return f"a path built from a command substitution that can reach the secrets file ({pattern})"
    return None


# ------------------------------------------------------------------------------------------------ shell
# The command is pre-processed before it is split: $'..' strings are decoded, every $(..), `..`, <(..) and >(..)
# becomes a placeholder (its body is checked as a command of its own, at the cwd where it runs), and a '$' inside
# single quotes becomes QDOLLAR so that it is not expanded. Words are then expanded like the shell would: variables
# (the environment, what bin/vc-lib.sh exports, assignments earlier in the command), ~, braces, globs and `cd`.
SUB_RE = re.compile(r"__VCSUB(\d+)__")
QDOLLAR = "\x02"
LOOSE = "\x01"          # stands for the output of a command substitution the guard cannot resolve
VAR_RE = re.compile(r"\$\{([^}]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def decode_ansi_c(cmd):
    def rep(m):
        raw = m.group(1)
        try:
            val = codecs.decode(raw.encode("latin-1", "backslashreplace"), "unicode_escape")
        except (UnicodeDecodeError, ValueError):
            val = raw
        return "'" + val.replace("'", "'\"'\"'") + "'"
    return re.sub(r"\$'((?:[^'\\]|\\.)*)'", rep, cmd)


def _close_paren(cmd, k):
    depth, i, sq, dq = 0, k, False, False
    while i < len(cmd):
        c = cmd[i]
        if c == "\\" and not sq:
            i += 2
            continue
        if c == "'" and not dq:
            sq = not sq
        elif c == '"' and not sq:
            dq = not dq
        elif not sq and not dq:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return len(cmd)


HEREDOC_RE = re.compile(r"(?<!<)<<(-?)[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([A-Za-z_][\w.-]*))")
HD_RE = re.compile(r"__VCHD(\d+)__")


def cut_heredocs(cmd):
    """Take heredoc bodies out of the command (they are data, or code for the program they feed).
    -> (command with `<< __VCHDk__` in place of each operator, [(body, quoted)])."""
    lines, out, docs, i = cmd.split("\n"), [], [], 0
    while i < len(lines):
        line, pending = lines[i], []

        def rep(m):
            delim = m.group(2) or m.group(3) or m.group(4)
            pending.append((delim, bool(m.group(2) or m.group(3) or m.group(0).find("\\") >= 0), m.group(1)))
            return f"<< __VCHD{len(docs) + len(pending) - 1}__"
        new = HEREDOC_RE.sub(rep, line)
        j, bodies = i + 1, []
        for delim, quoted, dash in pending:
            body = []
            while j < len(lines) and (lines[j].lstrip("\t") if dash else lines[j]) != delim:
                body.append(lines[j])
                j += 1
            if j >= len(lines):         # no terminator: not a heredoc after all (e.g. "<<" inside quotes)
                bodies = None
                break
            bodies.append(("\n".join(body), quoted))
            j += 1
        if bodies is None:
            out.append(line)
            i += 1
            continue
        docs += bodies
        out.append(new)
        i = j
    return "\n".join(out), docs


def preprocess(cmd):
    """-> (text, substitution bodies, heredocs)."""
    cmd, docs = cut_heredocs(cmd)
    text, bodies = scan_subs(cmd)
    for body, quoted in docs:
        if not quoted:                  # an unquoted heredoc runs its substitutions
            bodies += scan_subs(body)[1]
    return text, bodies, docs


def scan_subs(cmd):
    cmd = decode_ansi_c(cmd)
    out, bodies = [], []
    i, n, sq, dq = 0, len(cmd), False, False
    while i < n:
        c = cmd[i]
        if c == "\\" and not sq:
            nxt = cmd[i + 1:i + 2]
            out.append("\\" + (QDOLLAR if nxt == "$" else nxt))
            i += 2
            continue
        if c == "'" and not dq:
            sq = not sq
        elif sq:
            out.append(QDOLLAR if c == "$" else c)
            i += 1
            continue
        elif c == '"':
            dq = not dq
        elif (c == "$" or (c in "<>" and not dq)) and cmd[i + 1:i + 2] == "(":
            j = _close_paren(cmd, i + 1)
            bodies.append(cmd[i + 2:j])
            out.append(f"__VCSUB{len(bodies) - 1}__")
            i = j + 1
            continue
        elif c == "`":
            j = i + 1
            while j < n and cmd[j] != "`":
                j += 2 if cmd[j] == "\\" else 1
            bodies.append(cmd[i + 1:j])
            out.append(f"__VCSUB{len(bodies) - 1}__")
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out), bodies


def is_redirect(t):
    return bool(t) and set(t) <= set("<>&|0123456789") and ("<" in t or ">" in t)


def split_commands(cmd):
    """Simple commands: lists of words, split on ; && || | & and newlines. Redirect targets stay as '>' + word."""
    lx = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|<>()")
    lx.whitespace_split = True
    lx.commenters = ""
    try:
        toks = list(lx)
    except ValueError:
        toks = cmd.split()
    cmds, cur = [], []
    for t in toks:
        if t and set(t) <= set(";&|()"):
            if cur:
                cmds.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        cmds.append(cur)
    return cmds


ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\+?=(.*)$", re.S)


def strip_prefix(words):
    """Drop leading VAR=value assignments and sudo/nice/time wrappers."""
    i = 0
    while i < len(words) and (ASSIGN_RE.match(words[i]) or
                              words[i] in ("sudo", "nice", "time", "nohup", "command", "exec", "builtin")):
        i += 1
    return words[i:]


def redirect_targets(words):
    out, rest, i = [], [], 0
    while i < len(words):
        w = words[i]
        if is_redirect(w):
            if ">" in w and i + 1 < len(words):
                out.append(words[i + 1])
            i += 2
            continue
        rest.append(w)
        i += 1
    return out, rest


def _opt_value(args, i, j, takes):
    """Short-option cluster args[i] at char j takes a value: the rest of the cluster or the next word."""
    rest = args[i][j + 1:]
    if rest:
        return rest, i
    return (args[i + 1] if i + 1 < len(args) else ""), i + 1


def parse_opts(args, short_arg, long_arg):
    """-> ({option: [values]}, positionals). short_arg: short options that take a value; long_arg: long ones."""
    opts, pos, i = {}, [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos += args[i + 1:]
            break
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if not eq and name in long_arg:
                val = args[i + 1] if i + 1 < len(args) else ""
                i += 1
            opts.setdefault(name, []).append(val)
        elif a.startswith("-") and len(a) > 1:
            for j in range(1, len(a)):
                ch = "-" + a[j]
                if a[j] in short_arg:
                    val, i = _opt_value(args, i, j, short_arg)
                    opts.setdefault(ch, []).append(val)
                    break
                opts.setdefault(ch, []).append("")
        else:
            pos.append(a)
        i += 1
    return opts, pos


def _urls(pos):
    return [x for x in pos if re.match(r"^[A-Za-z][\w+.-]*://", x)]


def _url_name(u):
    return os.path.basename(u.split("://", 1)[-1].split("?")[0].split("#")[0].partition("/")[2])


def unwrap(words):
    """Drop assignments and wrappers (env, timeout, nice, sudo, ...) in front of the real program."""
    w = list(words)
    while w:
        if ASSIGN_RE.match(w[0]):
            w = w[1:]
            continue
        prog = os.path.basename(w[0])
        if prog not in WRAPPERS:
            break
        w = w[1:]
        while w and (w[0].startswith("-") or ASSIGN_RE.match(w[0]) or
                     (prog == "timeout" and re.match(r"^\d", w[0]))):
            if prog in ("nice", "ionice", "chrt", "taskset", "stdbuf", "sudo") and w[0] in ("-n", "-c", "-p", "-u",
                                                                                            "-g", "-o", "-e", "-i"):
                w = w[1:]
            w = w[1:]
    return w


CURL_TR = "--tra" "ce"        # curl's log-to-file option (split: C-40 keeps app names out of the hooks)


def mutated_paths(words):
    """-> (paths a simple command writes, moves or deletes; folders it may fill: archives, downloads). Best effort."""
    targets, words = redirect_targets(words)
    words = unwrap(words)
    dirs = []
    if not words:
        return targets, dirs
    prog = os.path.basename(words[0])
    raw = words[1:]
    args = [w for w in raw if not w.startswith("-")]
    if prog in MUTATORS:
        targets += [a.split("=", 1)[1] if a.startswith("of=") else a for a in args]
    elif prog in DEST_ONLY:
        short = {"cp": "St", "install": "gmoSt", "rsync": "eBfT"}.get(prog, "t")
        opts, pos = parse_opts(raw, short, {"--target-directory", "--suffix", "--mode", "--owner", "--group"})
        tdir = opts.get("-t", []) + opts.get("--target-directory", [])
        if tdir:
            targets += tdir
        elif pos:
            targets.append(pos[-1])
        if "--remove-source-files" in opts:
            targets += pos
    elif prog in INPLACE and any(w.startswith("-i") or w.startswith("--in-place") or w == "-pi" for w in raw):
        targets += args
    elif prog in ("awk", "gawk") and ("-iinplace" in raw or "--include=inplace" in raw or any(
            a in ("-i", "--include") and b == "inplace" for a, b in zip(raw, raw[1:]))):
        targets += args
    elif prog == "find" and any(w in ("-delete", "-exec", "-execdir") for w in words):
        targets += [w for w in raw if not w.startswith("-")][:1]
    elif prog in ("tar", "bsdtar", "gtar"):
        opts, pos = parse_opts(raw, "CfbFgHIKLNTVX", {"--directory", "--file", "--exclude", "--transform"})
        old = raw[0] if raw and re.fullmatch(r"[A-Za-z]+", raw[0]) else ""
        if ("-x" in opts or "--extract" in opts or "--get" in opts or "x" in old) and not (
                "-O" in opts or "--to-stdout" in opts):
            dirs += opts.get("-C", []) + opts.get("--directory", []) or ["."]
    elif prog == "unzip":
        opts, pos = parse_opts(raw, "dxP", set())
        if not any(o in opts for o in ("-l", "-t", "-v", "-Z", "-p", "-z")):
            dirs += opts.get("-d", []) or ["."]
    elif prog in ("7z", "7za", "7zr") and raw and raw[0] in ("x", "e"):
        dirs += [a[2:] for a in raw if a.startswith("-o")] or ["."]
    elif prog == "cpio":
        opts, pos = parse_opts(raw, "DEFHIMORs", {"--directory"})
        if "-i" in opts or "--extract" in opts:
            dirs += opts.get("-D", []) + opts.get("--directory", []) or ["."]
    elif prog == "curl":
        opts, pos = parse_opts(raw, "oDKdeEFHAbcrTuUwxXyYzCPmQt",
                               {"--output", "--dump-header", "--cookie-jar", CURL_TR, CURL_TR + "-ascii", "--stderr",
                                "--libcurl", "--etag-save", "--output-dir", "--hsts", "--alt-svc", "--data",
                                "--header", "--user", "--request", "--url", "--config"})
        for o in ("-o", "--output", "-D", "--dump-header", "-c", "--cookie-jar", CURL_TR, CURL_TR + "-ascii",
                  "--stderr", "--libcurl", "--etag-save", "--hsts", "--alt-svc"):
            targets += [v for v in opts.get(o, []) if v and v != "-"]
        if any(o in opts for o in ("-O", "--remote-name", "--remote-name-all", "-J", "--remote-header-name")):
            outdir = (opts.get("--output-dir") or ["."])[-1]
            for u in _urls(pos + opts.get("--url", [])):
                targets.append(os.path.join(outdir, _url_name(u)))
            dirs.append(outdir) if "-J" in opts or "--remote-header-name" in opts else None
    elif prog == "wget":
        opts, pos = parse_opts(raw, "OoaPeUtTwQBiDlXIAR",
                               {"--output-document", "--output-file", "--append-output", "--directory-prefix"})
        for o in ("-o", "--output-file", "-a", "--append-output"):
            targets += [v for v in opts.get(o, []) if v and v != "-"]
        docs = [v for v in opts.get("-O", []) + opts.get("--output-document", [])]
        if docs:
            targets += [v for v in docs if v and v != "-"]
        else:
            outdir = (opts.get("-P", []) + opts.get("--directory-prefix", []) or ["."])[-1]
            for u in _urls(pos):
                targets.append(os.path.join(outdir, _url_name(u) or "index.html"))
            if any(o in opts for o in ("-r", "--recursive", "-m", "--mirror", "-p", "--page-requisites")):
                dirs.append(outdir)
    elif prog == "sed":
        for x in raw:
            targets += re.findall(r"(?:^|[;}\s/])[wW]\s+(\S+)", x)
    if prog in ("awk", "gawk", "mawk", "nawk"):
        for x in raw:
            targets += re.findall(r">>?\s*\"([^\"]+)\"", x)
    return targets, dirs


def inner_scripts(words):
    """Code strings passed to sh -c / python -c etc."""
    words = unwrap(words)
    if not words:
        return [], []
    prog = os.path.basename(words[0])
    shells, codes = [], []
    for i, w in enumerate(words[1:-1], 1):
        if prog in SHELLS and w in ("-c", "-lc", "-ic"):
            shells.append(words[i + 1])
        if INTERP_RE.match(prog) and w in ("-c", "-e", "-E", "-r", "--eval"):
            codes.append(words[i + 1])
    return shells, codes


class Shell:
    """What the guard knows while it walks a command: cwd, variables, substitution bodies."""

    def __init__(self, cwd, owner, variables=None):
        self.cwd = norm(cwd or os.getcwd(), "/")
        self.owner = owner
        self.vars = dict(variables) if variables is not None else shell_vars()
        self.subs = []
        self.docs = []
        self.seen = set()


def expand_vars(s, sh):
    def rep(m):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        mm = re.match(r"([!#]?)([A-Za-z_][A-Za-z0-9_]*)(.*)$", inner, re.S)
        if not mm:
            return ""
        flag, name, rest = mm.groups()
        if flag == "!":
            name = sh.vars.get(name)
            if name is None or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                block("F-19(b)", MSG_SECRET, what="an indirect variable expansion the guard cannot resolve")
        val = sh.vars.get(name)
        if val is None and rest[:2] in (":-", ":="):
            val = expand_vars(rest[2:], sh)
        elif val is None and rest[:1] in ("-", "="):
            val = expand_vars(rest[1:], sh)
        if val is None:
            if SENSITIVE_NAME_RE.search(name):
                block("F-19(b)", MSG_SECRET, what=f"${name} (a config, settings or env-file variable the guard "
                                                  "cannot resolve)")
            return ""
        return str(len(val)) if flag == "#" else val
    return VAR_RE.sub(rep, s)


def brace_expand(s, limit=64):
    m = re.search(r"\{([^{}]*)\}", s)
    if not m:
        return [s]
    inner = m.group(1)
    if "," in inner:
        alts = inner.split(",")
    elif ".." in inner:
        alts = ["*"]
    else:
        return [s]
    out = []
    for a in alts:
        out += brace_expand(s[:m.start()] + a + s[m.end():], limit)
    return out[:limit]


def resolve_sub(body, sh):
    """The output of a simple substitution ($(echo ..), $(printf ..), $(pwd)), or None."""
    b = body.replace(QDOLLAR, "$").strip()
    if b == "pwd":
        return sh.cwd
    if re.search(r"[;&|<>`]|\$\(", b):
        return None
    try:
        w = shlex.split(b)
    except ValueError:
        return None
    if not w:
        return ""
    if w[0] == "echo":
        return " ".join(expand_plain(x, sh) for x in w[1:] if x not in ("-n", "-e", "-E"))
    if w[0] == "printf" and len(w) >= 2 and re.fullmatch(r"(%s)+(\\n)?", w[1]):
        return "".join(expand_plain(x, sh) for x in w[2:])
    if w[0] == "printf" and len(w) == 2 and "%" not in w[1]:
        return expand_plain(w[1], sh)
    return None


def expand_plain(word, sh):
    w = expand_vars(word, sh)
    return os.path.expanduser(w) if w.startswith("~") else w


def expand_word(word, sh):
    """-> the word's expansions ($'..', substitutions, variables, ~, braces). LOOSE marks unresolved output."""
    def sub_rep(m):
        k = int(m.group(1))
        body = sh.subs[k].replace(QDOLLAR, "$")
        if k not in sh.seen:
            sh.seen.add(k)
            check_bash(body, sh.cwd, sh.owner, fixer=False, variables=sh.vars)
        val = resolve_sub(sh.subs[k], sh)
        if val is None:
            if CONFIG_TEXT_RE.search(body):
                block("F-19(b)", MSG_SECRET, what="a command substitution that reads the settings, config or env "
                                                  "file (the guard cannot see what path it yields)")
            return LOOSE
        return val
    w = SUB_RE.sub(sub_rep, word)
    w = expand_vars(w, sh)
    if w.startswith("~"):
        w = os.path.expanduser(w)
    return [x.replace(QDOLLAR, "$") for x in brace_expand(w)]


def home_or_above(p):
    for home in {home_dir(), os.path.realpath(home_dir())}:
        if (home.rstrip("/") + "/").startswith(p.rstrip("/") + "/"):
            return True
    return False


def check_word_secret(text, sh, allow_root, home_ok=False):
    """Block when a (expanded) word names a credential or a folder holding one, in any form. home_ok: a command
    that does not read (echo, mkdir, ..) may name home itself or a folder above it."""
    if not text or text == LOOSE:
        return
    pieces = [text] + [x for x in re.split(r"[=:,]", text)[1:] if x]
    m = re.match(r"^-[A-Za-z]([/~.].*)$", text)
    if m:
        pieces.append(m.group(1))
    for i, pc in enumerate(pieces):
        if pc.startswith("~"):
            pc = os.path.expanduser(pc)
        if LOOSE in pc:
            if not pc.strip(LOOSE):
                continue
            pat = pc.replace(LOOSE, "*")
            if not pat.startswith(("/", "*")):
                pat = os.path.join(sh.cwd, pat)
            why = loose_hit(pat)
        elif GLOB_CHARS.search(pc):
            why = glob_hit(os.path.join(sh.cwd, pc))
        else:
            pathlike = "/" in pc or pc.startswith((".", "~"))
            if home_ok and home_or_above(norm(pc, sh.cwd)):
                continue
            why = secret_hit(os.path.join(sh.cwd, pc), pathlike=pathlike, allow_root=allow_root and i == 0)
        if why:
            block("F-19(b)", MSG_SECRET, what=why)


def reads_folder(w):
    prog = os.path.basename(w[0])
    if prog in FOLDER_READERS:
        return True
    if prog in GREPS and any(re.match(r"^-[A-Za-z]*[rR]", x) or x in ("--recursive", "--dereference-recursive")
                             or x.startswith("--directories=recurse") for x in w[1:]):
        return True
    if prog in ("cp", "scp") and any(re.match(r"^-[A-Za-z]*[rRa]", x) or x == "--recursive" for x in w[1:]):
        return True
    return prog == "git" and len(w) > 1 and w[1] == "grep"


def check_secret_literals(cmd):
    for s in sorted(secret_files()):
        if s in cmd:
            block("F-19(b)", MSG_SECRET, what=f"the secrets file {s}")
        home = home_dir()
        if s.startswith(home + "/") and ("~/" + s[len(home) + 1:]) in cmd:
            block("F-19(b)", MSG_SECRET, what=f"the secrets file {s}")


def check_text_secrets(cmd):
    for s in sorted(secret_files()):
        if s in cmd:
            block("F-19(b)", MSG_SECRET, what=f"the secrets file {s}")
        home = home_dir()
        if s.startswith(home + "/") and ("~/" + s[len(home) + 1:]) in cmd:
            block("F-19(b)", MSG_SECRET, what=f"the secrets file {s}")
    if re.search(r"(^|[^\w.-])vc\.env\b", cmd):
        block("F-19(b)", MSG_SECRET, what="the mounted secrets file")
    if PROFILE_TEXT_RE.search(cmd):
        block("F-19(b)", MSG_SECRET, what="the browser profile or its cookies")
    if SECRET_VAR_RE.search(cmd):
        block("F-19(b)", MSG_SECRET, what="an API key, token or env-file variable")
    if PROC_ENV_RE.search(cmd):
        block("F-19(b)", MSG_SECRET, what="the process environment")


def check_env_dump(w):
    prog = os.path.basename(w[0])
    if prog == "printenv" or "printenv" in w:
        block("F-19(b)", MSG_SECRET, what="printenv (the environment)")
    if prog == "env" and all(x.startswith("-") or "=" in x for x in w[1:]):
        block("F-19(b)", MSG_SECRET, what="env (the environment)")
    if w[-1] == "env" and prog in ("docker", "vc-env", "podman", "kubectl"):
        block("F-19(b)", MSG_SECRET, what="env inside the container")
    if prog == "set" and len(w) == 1:
        block("F-19(b)", MSG_SECRET, what="set (the environment)")
    if prog in ("export", "declare", "typeset") and (len(w) == 1 or any(x in ("-p", "-x") for x in w[1:])):
        block("F-19(b)", MSG_SECRET, what=f"{prog} (the environment)")
    if prog == "compgen" and any(x in ("-v", "-e") for x in w[1:]):
        block("F-19(b)", MSG_SECRET, what="compgen (the environment)")


def check_code(code, sh):
    """Inline interpreter code: environment access, and every string literal as a path."""
    if CODE_ENV_RE.search(code):
        block("F-19(b)", MSG_SECRET, what="the process environment (inline code)")
    if CODE_CONFIG_RE.search(code):
        block("F-19(b)", MSG_SECRET, what="a path into ~/.config (inline code)")
    for a, b in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", code):
        lit = a or b
        if lit.startswith("~") or "/" in lit or lit.startswith("."):
            check_word_secret(os.path.expanduser(lit), sh, allow_root=False)


def holds_qa(d):
    """The folder is (or lies above) a vc-v1 checkout, or holds part of the QA definition."""
    pre = d.rstrip("/") + "/"
    for r in set(roots_for(d)):
        rr = r.rstrip("/") + "/"
        if rr.startswith(pre):
            return r
        if pre.startswith(rr):
            rel = os.path.relpath(d, r)
            if any(g.startswith(rel + "/") for g in QA_GLOBS) or match_any(d, d, QA_GLOBS):
                return rel
    return None


def in_checkout(d):
    return any(d == r or d.startswith(r.rstrip("/") + "/") for r in roots_for(d) if is_vc_root(r)) or holds_qa(d)


def git_effect(w, cwd):
    """-> (repo folder, subcommand, paths it rewrites, rewrites at branch level)."""
    i, repo = 1, cwd
    while i < len(w) and w[i].startswith("-"):
        a = w[i]
        if a in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env") \
                and i + 1 < len(w):
            if a in ("-C", "--work-tree"):
                repo = norm(w[i + 1], repo)
            i += 2
            continue
        if a.startswith("--work-tree="):
            repo = norm(a.split("=", 1)[1], repo)
        i += 1
    sub, rest = (w[i], w[i + 1:]) if i < len(w) else ("", [])
    if sub not in GIT_TREE:
        return repo, sub, [], False
    if "--" in rest:
        k = rest.index("--")
        pre, paths = rest[:k], rest[k + 1:]
    else:
        pre, paths = rest, []
    opts = {x for x in pre if x.startswith("-") and x != "-"}
    pos = [x for x in pre if not x.startswith("-") or x == "-"]
    exists = [x for x in pos if x != "-" and os.path.exists(os.path.join(repo, x))]
    others = [x for x in pos if x not in exists]
    if sub in ("rm", "mv"):
        return repo, sub, pos + paths, False
    if sub == "reset":
        return repo, sub, [], bool(opts & {"--hard", "--merge", "--keep"})
    if sub == "stash":
        if pos and pos[0] in ("list", "show", "create", "drop", "clear", "store"):
            return repo, sub, [], False
        return repo, sub, paths, True
    if sub == "apply" and opts & {"--check", "--stat", "--numstat", "--summary"} and "--apply" not in opts:
        return repo, sub, [], False
    if sub == "clean":
        if any(re.match(r"^-[A-Za-z]*n", o) for o in opts) or "--dry-run" in opts:
            return repo, sub, [], False
        return repo, sub, pos + paths, not (pos + paths)
    if sub == "read-tree" and "-u" not in opts:
        return repo, sub, [], False
    if sub in ("checkout", "switch") and opts & {"-b", "-B", "--orphan", "-c", "-C", "--create", "--force-create"}:
        return repo, sub, paths, len(pos) > 1
    if sub == "restore":
        src = {pre[k + 1] for k, x in enumerate(pre[:-1]) if x in ("-s", "--source")}
        paths = [x for x in pos if x not in src] + paths
        return repo, sub, paths, not paths
    if sub in ("checkout", "switch"):
        return repo, sub, exists + paths, bool(others)
    return repo, sub, paths, True


def check_code_protected(code, sh, what="inline code"):
    """Code run by an interpreter (-c/-e, heredoc, here-string, stdin): writes that name the QA definition."""
    if ".approved" in code:
        block("F-19(a)", MSG_APPROVAL)
    if sh.owner or not WRITE_CODE_RE.search(code):
        return
    m = PROTECTED_FRAG_RE.search(code)
    if m:
        block("F-19(a)", MSG_QA, what=f"{what} writing near {m.group(0)}")
    for a, b in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", code):
        lit = a or b
        if lit and not re.search(r"\s", lit):
            hit = is_qa(norm(lit, sh.cwd), sh.cwd) or (holds_qa(norm(lit, sh.cwd)) if lit in (".", "./") else None)
            if hit:
                block("F-19(a)", MSG_QA, what=f"{what} writing {hit}")


def check_target(t, sh, base=None):
    p = norm(t.replace(LOOSE, "*"), base or sh.cwd)
    if is_approval_dir(p):
        block("F-19(a)", MSG_APPROVAL)
    if p.endswith(".toml") and match_any(p, sh.cwd, ["specs/*"]):
        block("F-19(a)", MSG_SPEC_SHELL)
    hit = is_qa(p, sh.cwd)
    if hit and not sh.owner:
        block("F-19(a)", MSG_QA, what=hit)
    return p


def stdin_code(w):
    """'shell' or 'interp' when the program reads its code from stdin, else None."""
    prog = os.path.basename(w[0])
    args = w[1:]
    if prog in SHELLS:
        if any(x in ("-c", "-lc", "-ic") for x in args):
            return None
        pos = [x for x in args if not x.startswith("-")]
        return "shell" if not pos or "-s" in args else None
    if INTERP_RE.match(prog):
        if any(x in ("-c", "-e", "-E", "-m", "-r", "--eval", "-p", "--version", "-V") for x in args):
            return None
        pos = [x for x in args if not x.startswith("-") or x == "-"]
        return "interp" if not pos or pos[0] == "-" else None
    return None


def check_protected_words(words, sh, cmd, docs):
    owner = sh.owner
    targets, dirs = mutated_paths(words)
    for t in targets:
        check_target(t, sh)
    for d in dirs:
        p = norm(d.replace(LOOSE, "*"), sh.cwd)
        hit = is_qa(p, sh.cwd) or holds_qa(p)
        if hit and not owner:
            block("F-19(a)", MSG_QA, what=f"unpacking or downloading into {hit}")
    w = unwrap(redirect_targets(words)[1])
    if not w:
        return
    prog = os.path.basename(w[0])
    if prog == "git":
        repo, sub, paths, branch = git_effect(w, sh.cwd)
        for t in paths:
            p = check_target(t, sh, base=repo)
            hit = holds_qa(p)
            if hit and not owner:
                block("F-19(a)", MSG_QA, what=f"git {sub} over {hit}")
        if branch and not owner and in_checkout(repo):
            block("F-19(a)", MSG_QA, what=f"git {sub} (rewrites the working tree, QA definition included)")
    if prog in ("xargs", "parallel") and not owner and PROTECTED_FRAG_RE.search(cmd):
        inner = [x for x in w[1:] if not x.startswith("-")]
        if inner and (os.path.basename(inner[0]) in MUTATORS | DEST_ONLY | INPLACE or inner[0] == "git"):
            block("F-19(a)", MSG_QA, what=f"{prog} {inner[0]} fed with QA paths")
    shells, codes = inner_scripts(words)
    for s in shells:
        check_bash(s, sh.cwd, owner, fixer=False, variables=sh.vars)
    for c in codes:
        check_code(c, sh)
        check_code_protected(c, sh)
    kind = stdin_code(w)
    if kind:
        bodies = []
        for a, b in zip(words, words[1:]):
            if a == "<<<":
                bodies.append(b)
            m = HD_RE.fullmatch(b) if a in ("<<", "<<-") else None
            if m:
                bodies.append(docs[int(m.group(1))][0])
        if not bodies:                  # fed through a pipe: judge the whole command line
            if kind == "interp":
                bodies = [cmd]
            else:
                if not owner and (PROTECTED_FRAG_RE.search(cmd) or re.search(r"\b(gate|hooks)\b", cmd)):
                    block("F-19(a)", MSG_QA, what="a shell reading commands from a pipe that names the QA "
                                                  "definition")
                bodies = [x for x in words if re.search(r"\s", x)]   # quoted strings may be the piped commands
        for body in bodies:
            if kind == "shell":
                check_bash(body, sh.cwd, owner, fixer=False, variables=sh.vars)
            else:
                check_code(body, sh)
                check_code_protected(body, sh, what=f"code fed to {prog}")


def walk(cmd, text, words_list, sh):
    """One pass over the simple commands: cd, assignments, credentials, the QA definition."""
    for raw in words_list:
        # assignments (VAR=.. prefixes, export/local/declare/readonly VAR=..)
        if raw and raw[0] in ("export", "local", "declare", "readonly", "typeset"):
            lead = raw[1:]
        else:
            lead = list(itertools.takewhile(lambda x: ASSIGN_RE.match(x), raw))
        for x in lead:
            m = ASSIGN_RE.match(x)
            if not m:
                continue
            val = expand_word(m.group(2), sh)[0] if m.group(2) else ""
            check_word_secret(val, sh, allow_root=False)
            sh.vars[m.group(1)] = val
        words = []
        for x in raw:
            if is_redirect(x):
                words.append(x)
                continue
            words += expand_word(x, sh) or [""]
        w = strip_prefix([x for x in words if not is_redirect(x)])
        if not w:
            continue
        prog = os.path.basename(w[0])
        if prog in ("cd", "pushd"):
            args = [x for x in w[1:] if not x.startswith("-") or x == "-"]
            dest = args[0] if args else sh.vars.get("HOME", home_dir())
            if dest != "-" and LOOSE not in dest:
                sh.cwd = norm(dest, sh.cwd)
            continue
        folder = reads_folder(w)
        for x in words:
            if not is_redirect(x):
                check_word_secret(x, sh, allow_root=folder, home_ok=prog in NON_READERS)
        if folder:
            why = secret_hit(sh.cwd)
            if why:
                block("F-19(b)", MSG_SECRET, what=f"{prog} in {why}")
        if prog in ("xargs", "parallel", "read", "mapfile", "readarray") and CONFIG_TEXT_RE.search(cmd):
            block("F-19(b)", MSG_SECRET, what=f"{prog} fed from the settings, config or env file")
        if prog == "eval":
            check_bash(" ".join(w[1:]), sh.cwd, sh.owner, fixer=False, variables=sh.vars)
        check_env_dump(w)
        check_protected_words(words, sh, cmd, sh.docs)


def check_fixer_bash(cmd, words_list):
    if re.search(r"[;&|<>`\n]|\$\(", cmd):
        block("F-19(c)", MSG_FIXER_BASH)
    if len(words_list) != 1 or not strip_prefix(words_list[0]):
        block("F-19(c)", MSG_FIXER_BASH)
    w = strip_prefix(words_list[0])
    prog = os.path.basename(w[0])
    if prog in FIXER_PLAIN:
        if prog in ("grep", "rg") and any(x.startswith("--pre") for x in w):
            block("F-19(c)", MSG_FIXER_BASH)
        return
    sub = w[1] if len(w) > 1 else ""
    if prog in FIXER_BASH and sub in FIXER_BASH[prog]:
        return
    if prog == "vc-spec" and sub == "approve":
        block("F-19(c)", MSG_APPROVAL)
    block("F-19(c)", MSG_FIXER_BASH)


def check_bash(cmd, cwd, owner, fixer, variables=None):
    if re.search(r"\b" + OWNER_FLAG + r"\b", cmd):
        block("F-19(a)", MSG_FLAG)
    check_text_secrets(cmd)
    text, bodies, docs = preprocess(cmd)
    words_list = split_commands(text)
    if fixer:
        check_fixer_bash(cmd, words_list)
    sh = Shell(cwd, owner, variables)
    sh.subs = bodies
    sh.docs = docs
    walk(cmd, text, words_list, sh)
    for k, body in enumerate(bodies):       # substitutions in places the walk did not expand
        if k not in sh.seen:
            check_bash(body.replace(QDOLLAR, "$"), cwd, owner, fixer=False, variables=sh.vars)


# ------------------------------------------------------------------------------------------------ files
def approval_section(text):
    m = re.search(r"(?m)^\s*\[approval\]", text or "")
    return (text[m.start():], m.start()) if m else (None, None)


def check_spec_edit(path, tool, ti):
    if not path.endswith(".toml"):
        return
    try:
        with open(path) as f:
            cur = f.read()
    except OSError:
        cur = None
    if tool == "Write":
        new = ti.get("content") or ""
        if cur is None:
            if approval_section(new)[0] is not None:
                block("F-19(a)", MSG_APPROVAL)
        elif approval_section(cur)[0] != approval_section(new)[0]:
            block("F-19(a)", MSG_APPROVAL)
        return
    edits = ti.get("edits") if tool == "MultiEdit" else [ti]
    sect, start = approval_section(cur)
    for e in edits or []:
        old, new = e.get("old_string") or "", e.get("new_string") or ""
        if "[approval]" in old or "[approval]" in new:
            block("F-19(a)", MSG_APPROVAL)
        if sect is not None and old:
            idx = cur.find(old)
            if idx >= 0 and idx + len(old) > start:
                block("F-19(a)", MSG_APPROVAL)
            if old in sect:
                block("F-19(a)", MSG_APPROVAL)


def check_file_tool(tool, ti, cwd, owner, fixer):
    raw = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
    if tool in ("Grep", "Glob"):
        base = norm(ti.get("path") or cwd, cwd)
        why = secret_hit(base)
        if why:
            block("F-19(b)", MSG_SECRET, what=why)
        keys = ("glob", "pattern") if tool == "Glob" else ("glob",)
        for key in keys:
            v = ti.get(key)
            if not v:
                continue
            pat = os.path.join(base, os.path.expanduser(v))
            why = glob_hit(pat) if GLOB_CHARS.search(v) else secret_hit(pat, allow_root=False)
            if why:
                block("F-19(b)", MSG_SECRET, what=why)
        v = ti.get("pattern")
        if v and tool == "Grep" and ("/" in v or v.startswith("~") or v.startswith(".")):
            why = secret_hit(norm(v, cwd), allow_root=False)
            if why:
                block("F-19(b)", MSG_SECRET, what=why)
        v = ti.get("pattern")
        if v and tool == "Glob" and re.search(r"(^|/)\.env$|vc\.env|Cookies|Login Data", v):
            block("F-19(b)", MSG_SECRET, what="a credential file pattern")
        return
    if not raw:
        return
    path = norm(raw, cwd)
    why = secret_hit(path, allow_root=True) if tool == "Read" else secret_reason(path)
    if why:
        block("F-19(b)", MSG_SECRET, what=why)
    if tool == "Read":
        return
    body = json.dumps(ti)
    if re.search(OWNER_FLAG + r"\s*[=:]", body) and not owner:
        block("F-19(a)", MSG_FLAG)
    if is_approval_dir(path):
        block("F-19(a)", MSG_APPROVAL)
    if fixer:
        if tool == "Write":
            if not re.search(r"/fixes/fix-\d+\.json$", path):
                block("F-19(c)", MSG_FIXER_WRITE)
            return
        if not (path.endswith(".toml") and match_any(path, cwd, ["specs/*"])) and not match_any(
                path, cwd, FIXER_EDIT_GLOBS):
            block("F-19(c)", MSG_FIXER_EDIT)
    hit = is_qa(path, cwd)
    if hit and not owner:
        block("F-19(a)", MSG_QA, what=hit)
    check_spec_edit(path, tool, ti)


# tools whose input is plain text for a model, a person or the web (no local file is read)
PLAIN_TEXT_TOOLS = {"Agent", "Task", "SendMessage", "WebSearch", "WebFetch", "TodoWrite", "AskUserQuestion",
                    "SubagentHandback", "Skill", "ExitPlanMode", "EnterPlanMode", "ToolSearch", "TaskStop",
                    "TaskOutput", "BashOutput", "KillShell", "KillBash"}
COMMAND_TOOLS = {"Monitor"}             # tools that run a shell command: the full Bash check
PATH_KEY_RE = re.compile(r"(?i)path|file|dir|folder|cwd|root|location|target|src|dest|source")


def _strings(v, key=""):
    if isinstance(v, str):
        yield key, v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _strings(x, str(k))
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x, key)


def check_generic(tool, ti, cwd, owner, fixer):
    """Any other tool (MCP, Monitor, ...): every string in its input, at any depth, must not name the secrets
    file or a folder holding it, after expansion. A path-like key (path, file, dir, ...) is read as a path."""
    if tool in COMMAND_TOOLS and isinstance(ti.get("command"), str):
        check_bash(ti["command"], cwd, owner, fixer)
    sh = Shell(cwd, owner)
    for key, val in _strings(ti):
        if tool in COMMAND_TOOLS and key == "command":
            continue
        check_secret_literals(val)
        pathkey = bool(PATH_KEY_RE.search(key))
        toks = [val.strip()] if pathkey else re.split(r"[\s'\"(),;<>|`]+", val)
        for tok in toks:
            if not tok or not (pathkey or tok.startswith(("~", "/", "$", "."))):
                continue
            for x in expand_word(tok, sh) if "$" in tok or "{" in tok else [os.path.expanduser(tok)]:
                check_word_secret(x, sh, allow_root=pathkey, home_ok=not pathkey)


def is_fixer(data):
    at = str(data.get("agent_type") or "")
    return at.split(":")[-1] == "fixer" or os.environ.get("VC_AGENT_ROLE") == "fixer"


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    owner = os.environ.get(OWNER_FLAG) == "1"
    fixer = is_fixer(data)
    try:
        if tool == "Bash":
            check_bash(str(ti.get("command") or ""), cwd, owner, fixer)
        elif tool in ("Read", "Grep", "Glob", "Edit", "Write", "MultiEdit", "NotebookEdit"):
            check_file_tool(tool, ti, cwd, owner, fixer)
        elif tool not in PLAIN_TEXT_TOOLS and isinstance(ti, dict):
            check_generic(tool, ti, cwd, owner, fixer)
    except Block as b:
        print(str(b), file=sys.stderr)
        return 2
    except Exception as e:              # fail closed: a guard that crashes must not let the call through
        print(f"vc-v1 guard blocked this (F-19): the guard could not judge this call ({type(e).__name__}). "
              "Split it into simpler commands.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
