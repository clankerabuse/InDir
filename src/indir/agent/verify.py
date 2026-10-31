"""Ground-truth checks that a request was actually carried out.

The agent's own words are not evidence. A shell command exiting 0 is not
evidence either: `for f in *.jpeg; do mv -- "$f" "${f%.jpeg}.jpg"; done` exits
0 on a directory with no jpegs, and `ls *.mp4` exits 0 without touching
anything. So this module works from three independent signals instead:

1. Effects — the real directory state before the turn vs. after it.
2. Declared outputs — the paths a command says it will produce, read from the
   command the agent actually ran.
3. Claims — filenames the model says it created or removed, checked against
   the filesystem.

A turn that fails these checks is handed back to the model with the specific
discrepancy, and is never reported as done.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

# A directory agent works in folders of ordinary size; the cap keeps a turn from
# stalling on a stray tree of thousands of files (node_modules, a photo dump).
_MAX_SNAPSHOT_ENTRIES = 20000
_SKIP_DIRS = {".git"}


class FileStat(NamedTuple):
    mtime_ns: int
    size: int
    is_dir: bool


# ---------------------------------------------------------------------------
# 1. Effects: the directory before and after
# ---------------------------------------------------------------------------


def snapshot_directory(
    directory: Path, limit: int = _MAX_SNAPSHOT_ENTRIES
) -> dict[str, FileStat]:
    """Record every file and directory under `directory`, keyed by relative path."""
    snapshot: dict[str, FileStat] = {}
    if not directory.is_dir():
        return snapshot

    stack = [directory]
    while stack and len(snapshot) < limit:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if len(snapshot) >= limit:
                        break
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir and entry.name in _SKIP_DIRS:
                        continue
                    try:
                        key = os.path.relpath(entry.path, directory)
                    except ValueError:
                        continue
                    snapshot[key] = FileStat(stat.st_mtime_ns, stat.st_size, is_dir)
                    if is_dir:
                        stack.append(Path(entry.path))
        except OSError:
            continue
    return snapshot


@dataclass(frozen=True)
class Effects:
    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.created or self.modified or self.deleted)

    def describe(self, limit: int = 4) -> str:
        parts: list[str] = []
        for verb, names in (
            ("created", self.created),
            ("modified", self.modified),
            ("deleted", self.deleted),
        ):
            if not names:
                continue
            shown = ", ".join(names[:limit])
            if len(names) > limit:
                shown += f" (+{len(names) - limit} more)"
            parts.append(f"{verb} {shown}")
        return "; ".join(parts)


def diff_snapshots(before: dict[str, FileStat], after: dict[str, FileStat]) -> Effects:
    before_keys = set(before)
    after_keys = set(after)
    created = tuple(sorted(after_keys - before_keys))
    deleted = tuple(sorted(before_keys - after_keys))
    # A directory's own mtime is bumped by every write inside it, so it would
    # report a "modification" for work its children already describe.
    modified = tuple(
        sorted(
            key
            for key in before_keys & after_keys
            if not after[key].is_dir and before[key] != after[key]
        )
    )
    return Effects(created=created, modified=modified, deleted=deleted)


# ---------------------------------------------------------------------------
# 2. Intent: did the user ask for a change, or for information?
# ---------------------------------------------------------------------------

_MUTATING_VERB_RE = re.compile(
    r"\b(?:convert|converts|compress|compressing|re-?encode|transcod\w+|"
    r"zip|unzip|archive|extract|untar|"
    r"move|rename|copy|duplicate|"
    r"delete|remove|erase|purge|"
    r"create|make|write|generate|produce|export|save|"
    r"resize|scale|crop|rotate|merge|split|join|trim|"
    r"download|install|"
    r"clean(?:ing)?\s*up|cleanup|organi[sz]e|sort|dedupe|deduplicate|"
    r"fix|repair|watermark|encrypt|decrypt|flatten|"
    r"backup|replace|overwrite|chmod|touch)\b",
    re.IGNORECASE,
)

_READ_ONLY_VERB_RE = re.compile(
    r"\b(?:list|show|tell|count|find|search|look|check|inspect|read|"
    r"print|display|summari[sz]e|report|view|peek|explain|describe)\b",
    re.IGNORECASE,
)

# "how do i convert a video" is a question about a conversion, not a request
# for one. "can you convert this" is a request, so it is deliberately absent.
_INFORMATIONAL_START_RE = re.compile(
    r"^(?:how|what|why|which|where|when|who|whose|is|are|was|were|does|did|"
    r"explain|describe|tell|show|list|find|search|count)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RequestIntent:
    is_action: bool
    verbs: tuple[str, ...] = ()


def classify_request(text: str) -> RequestIntent:
    """Action vs. informational, from the shape of the request itself.

    This only decides whether "nothing changed" is a failure on its own; a
    mutating command that changes nothing fails either way.
    """
    stripped = text.strip()
    verbs = tuple(sorted({m.group(0).lower() for m in _MUTATING_VERB_RE.finditer(stripped)}))
    if not verbs:
        return RequestIntent(is_action=False)
    if _INFORMATIONAL_START_RE.match(stripped):
        return RequestIntent(is_action=False, verbs=verbs)

    first_action = _MUTATING_VERB_RE.search(stripped)
    first_read = _READ_ONLY_VERB_RE.search(stripped)
    if first_read is not None and first_action is not None and first_read.start() < first_action.start():
        # "list the mp4s and convert them" reads as a question first; a real
        # mutation still has to show up in the directory diff.
        return RequestIntent(is_action=False, verbs=verbs)
    return RequestIntent(is_action=True, verbs=verbs)


# ---------------------------------------------------------------------------
# 3. Declared outputs: what a command says it will produce
# ---------------------------------------------------------------------------

# Shell loops are split too: `for f in *.jpeg; do mv ...; done` only does
# anything inside `do ... done`, and that body is a command like any other.
# `{`/`}` are not separators: `${f%.jpeg}.jpg` must survive tokenising intact.
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||\||;|\n|\bdo\b|\bthen\b|\belse\b|\bdone\b")
_REDIRECT_RE = re.compile(r"(?:^|[^>])>>?\s*([^\s;|&<>]+)")

# Programs whose declared purpose is to write a file. Anything else is ignored
# rather than guessed at: a python one-liner used for reading must not be
# mistaken for a failed conversion. Redirections are handled separately.
_MUTATING_PROGRAMS = {
    "ffmpeg", "avconv", "sox", "magick", "convert", "gm", "pandoc", "gs",
    "rsvg-convert", "cwebp", "dwebp", "pngquant", "optipng", "jpegoptim",
    "opusenc", "flac", "lame", "inkscape", "img2pdf", "pdftoppm", "pdftotext",
    "qpdf", "pdftk", "zip", "unzip", "tar", "7z", "7za", "7zr", "rar",
    "unrar", "mv", "cp", "install", "ln", "mkdir", "touch", "truncate",
    "tee", "dd", "wget", "curl", "yt-dlp", "youtube-dl",
}
_ARCHIVE_PROGRAMS = {"zip", "unzip", "tar", "7z", "7za", "7zr", "rar", "unrar"}
_DESTRUCTIVE_PROGRAMS = {"rm", "shred", "unlink", "rmdir"}

# Output flags are per-program: `-t` is a duration for ffmpeg and a target
# directory for mv, and guessing wrong would invent a file that never existed.
_OUTPUT_FLAGS: dict[str, tuple[str, ...]] = {
    "wget": ("-O", "--output-document"),
    "curl": ("-o", "--output"),
    "yt-dlp": ("-o", "--output"),
    "youtube-dl": ("-o", "--output"),
    "pandoc": ("-o", "--output"),
    "gs": ("-o",),
    "qpdf": ("-o",),
    "sox": ("-o",),
}
_TARGET_FLAGS: dict[str, tuple[str, ...]] = {
    "mv": ("-t", "--target-directory"),
    "cp": ("-t", "--target-directory"),
    "ln": ("-t", "--target-directory"),
    "install": ("-t", "--target-directory"),
    "unzip": ("-d",),
    "tar": ("-C",),
    "7z": ("-o",),
    "7za": ("-o",),
    "7zr": ("-o",),
}


@dataclass(frozen=True)
class CommandEffect:
    mutating: bool = False
    destructive: bool = False
    outputs: tuple[str, ...] = ()
    removals: tuple[str, ...] = ()
    redirects: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return [part.strip("\"'") for part in segment.split()]


_SHELL_NOISE = {"{", "}", "(", ")", "!", "elif", "fi", "then", "do", "done", "while", "until"}

# `sudo` and friends wrap the real command; shell keywords precede it.
_WRAPPERS = {"command", "env", "time", "nohup", "nice", "exec", "builtin"}


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Drop `VAR=value` prefixes, shell keywords and env/command wrappers."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            index += 1
            continue
        if token in _SHELL_NOISE or token in _WRAPPERS:
            index += 1
            continue
        break
    return tokens[index:]


def _looks_like_path(token: str) -> bool:
    if not token or token.startswith("-"):
        return False
    # `$VAR`, `%(title)s.%(ext)s` and `*.mp4` name no single file, so they can
    # never be checked for existence: the directory diff covers those instead.
    if any(char in token for char in '$%{}*?='):
        return False
    if token in {".", "..", "~"}:
        return True
    name = token.rstrip("/").replace("\\", "/").rsplit("/", 1)[-1]
    return bool(name) and ("." in name or "/" in token)


def _looks_like_dest(token: str) -> bool:
    """A path given to `-d`/`-C`/`-t`: a directory may have no extension at all."""
    if not token or token.startswith("-"):
        return False
    return not any(char in token for char in "$%{}*?=")


def _value_after(args: list[str], flag: str) -> str | None:
    for index, token in enumerate(args):
        if token == flag or token.startswith(flag + "="):
            if "=" in token:
                return token.split("=", 1)[1]
            if index + 1 < len(args):
                return args[index + 1]
            return None
    return None


def _last_path(args: list[str]) -> str | None:
    for token in reversed(args):
        if _looks_like_path(token):
            return token
    return None


def _first_path(args: list[str]) -> str | None:
    for token in args:
        if _looks_like_path(token):
            return token
    return None


def _archive_output(program: str, args: list[str]) -> str | None:
    """The archive a compress/create invocation writes, if this is one."""
    switches = " ".join(token for token in args if token.startswith("-")).lower()
    if "t" in switches and program == "tar":
        return None  # listing an archive writes nothing
    if any(switch in args for switch in ("--extract", "-x", "-e")):
        return None  # extraction: the target directory's diff is the check
    if program == "tar" and "x" in switches:
        return None
    if program in {"zip", "rar"} or (program in {"7z", "7za", "7zr"} and "a" in args):
        flagged = next(
            (_value_after(args, flag) for flag in ("-o", "-O") if _value_after(args, flag)),
            None,
        )
        return flagged or _first_path(args)
    if program == "tar" and "c" in switches:
        # `tar -czf out.tar.gz src`: the archive follows the flag cluster.
        if "--file" in args:
            return _value_after(args, "--file")
        for index, token in enumerate(args):
            if token.startswith("-") and not token.startswith("--") and "f" in token[1:]:
                if index + 1 < len(args):
                    return args[index + 1]
        return None
    return None


# Openers of a shell construct; the command itself is in the `do ... done`
# body or after `then`, which the splitter already separates.
_CONSTRUCT_OPENERS = {"for", "while", "until", "if", "elif", "case", "select"}


def segments(command: str) -> list[tuple[str, list[str]]]:
    """Split a shell command into (program, args) pairs.

    Exposed so the approval policy grades each command by its own arguments
    rather than by flags picked up from a neighbour in the same line.
    """
    found: list[tuple[str, list[str]]] = []
    for segment in _SEGMENT_SPLIT_RE.split(command):
        raw = _tokens(segment)
        if not raw or raw[0] in _CONSTRUCT_OPENERS:
            continue
        tokens = _strip_wrappers(raw)
        # `command -v ffmpeg` survives wrapper-stripping as `-v ffmpeg`: a flag
        # is not a program.
        if not tokens or tokens[0].startswith("-"):
            continue
        found.append((tokens[0].rsplit("/", 1)[-1], tokens[1:]))
    return found


def analyze_command(command: str) -> CommandEffect:
    """Read one shell command for mutation and the outputs it promises.

    Deliberately shallow: it recognises the programs this agent actually runs.
    Anything it misses is still covered by the before/after directory diff.
    """
    mutating = False
    destructive = False
    outputs: list[str] = []
    removals: list[str] = []
    programs: list[str] = []

    for program, args in segments(command):
        programs.append(program)

        # `dd of=out.img` carries its destination inside an argument.
        outputs.extend(
            token.split("=", 1)[1] for token in args if token.startswith("of=")
        )

        if program in _DESTRUCTIVE_PROGRAMS:
            mutating = True
            destructive = True
            # A deletion is proven by the operand being gone, which also works
            # for a path outside the folder this chat is scoped to.
            removals.extend(
                token
                for token in args
                if token not in {"--", "-f", "-r", "-R", "--force", "--recursive"}
                and _looks_like_dest(token)
            )
            continue
        if program not in _MUTATING_PROGRAMS:
            continue
        mutating = True

        if program in _ARCHIVE_PROGRAMS:
            archive = _archive_output(program, args)
            if archive is not None and _looks_like_path(archive):
                outputs.append(archive)
            target = next(
                (
                    _value_after(args, flag)
                    for flag in _TARGET_FLAGS.get(program, ())
                    if _value_after(args, flag)
                ),
                None,
            )
            if target and _looks_like_dest(target):
                outputs.append(target)
            continue

        chosen = next(
            (
                _value_after(args, flag)
                for flag in _OUTPUT_FLAGS.get(program, ())
                if _value_after(args, flag)
            ),
            None,
        )
        dest = next(
            (
                _value_after(args, flag)
                for flag in _TARGET_FLAGS.get(program, ())
                if _value_after(args, flag)
            ),
            None,
        )
        if dest is not None:
            if _looks_like_dest(dest):
                outputs.append(dest)
            continue
        if chosen is None:
            # For these tools the output is the last argument; flag values such
            # as `-c:v libvpx-vp9` never sit at the end of a real invocation.
            chosen = _last_path(args)
        if chosen is not None and _looks_like_path(chosen):
            outputs.append(chosen)

    redirects = [t for t in _REDIRECT_RE.findall(command) if _looks_like_path(t)]
    outputs.extend(redirects)

    unique: list[str] = []
    for path in outputs:
        if path not in unique:
            unique.append(path)
    return CommandEffect(
        mutating=mutating,
        destructive=destructive,
        outputs=tuple(unique),
        removals=tuple(dict.fromkeys(removals)),
        redirects=tuple(dict.fromkeys(redirects)),
        programs=tuple(dict.fromkeys(programs)),
    )


def resolve_outputs(directory: Path, outputs: tuple[str, ...]) -> list[Path]:
    paths: list[Path] = []
    for raw in outputs:
        try:
            candidate = Path(raw).expanduser()
        except (OSError, ValueError):
            continue
        if not candidate.is_absolute():
            candidate = directory / candidate
        paths.append(candidate)
    return paths


# ---------------------------------------------------------------------------
# 4. Claims: filenames the model says it created or removed
# ---------------------------------------------------------------------------

_CREATE_VERB_RE = re.compile(
    r"\b(?:created|creates?|creating|saved|saves?|saving|wrote|written|writes?|writing|"
    r"generated|generates?|generating|produced|produces?|producing|converted|converts?|"
    r"converting|exported|exports?|exporting|extracted|extracts?|extracting|"
    r"unzipped|unzipping|copied|copies|copying|moved|moves?|moving|renamed|renames?|"
    r"renaming|downloaded|downloads?|downloading|compressed|compresses?|compressing|"
    r"zipped|zipping|resized|resizes?|resizing|cropped|crops?|cropping|"
    r"installed|installs?|installing|merged|merges?|merging|trimmed|trims?|trimming|"
    r"backed\s+up|deduplicated)\b",
    re.IGNORECASE,
)
_DELETE_VERB_RE = re.compile(
    r"\b(?:deleted|deletes?|deleting|removed|removes?|removing|erased|erases?|erasing)\b",
    re.IGNORECASE,
)
_TARGET_MARKER_RE = re.compile(r"(?:\b(?:to|into|as|named|called|at)\b|→|->|=>)", re.IGNORECASE)

_QUOTED_CANDIDATE_RE = re.compile(r"`([^`\n]+)`|\"([^\"\n]+)\"|'([^'\n]+)'")
_BARE_CANDIDATE_RE = re.compile(
    r"(?<![\w/.\-])((?:~|\.\.?/)?(?:[\w.\-]+/)*[\w\-][\w.\-]*\.[A-Za-z0-9]{1,8})(?![\w\-])"
)
_EXTENSION_OK_RE = re.compile(r"^[A-Za-z0-9]{1,8}$")
_NOT_A_FILE = {"e.g", "i.e", "etc", "n/a", "vs"}


@dataclass(frozen=True)
class Claim:
    kind: str  # "created" | "deleted"
    path: Path
    raw: str


def _candidates(text: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for match in _QUOTED_CANDIDATE_RE.finditer(text):
        value = next(group for group in match.groups() if group is not None).strip()
        if value:
            found.append((match.start(), value))
    for match in _BARE_CANDIDATE_RE.finditer(text):
        name = match.group(1).strip(".,;:)")
        if name:
            found.append((match.start(), name))
    found.sort(key=lambda item: item[0])
    return [value for _, value in found]


def _plausible(raw: str) -> bool:
    if not raw or len(raw) > 200:
        return False
    if raw.startswith("-") or "://" in raw or "$" in raw or "\n" in raw:
        return False
    if raw.lower() in _NOT_A_FILE:
        return False
    cleaned = raw.strip().rstrip("/")
    if not cleaned or cleaned.startswith("."):
        return False
    name = cleaned.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or name.startswith("-"):
        return False
    if re.fullmatch(r"[\d.]+", name):
        return False
    suffix = name.rsplit(".", 1)[-1] if "." in name else ""
    if suffix and not _EXTENSION_OK_RE.match(suffix):
        return False
    return bool(suffix) or "/" in cleaned


def extract_claims(text: str, directory: Path, before: set[str]) -> list[Claim]:
    """Filenames the reply presents as the result of this turn."""
    claims: list[Claim] = []
    for sentence in re.split(r"\n+|(?<=[.!?])\s+", text):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?"):
            continue
        lowered = sentence.lower()
        if "if you" in lowered or "would like" in lowered or "you could" in lowered:
            continue

        spans: list[tuple[int, str]] = [
            (match.start(), "created") for match in _CREATE_VERB_RE.finditer(sentence)
        ]
        spans += [
            (match.start(), "deleted") for match in _DELETE_VERB_RE.finditer(sentence)
        ]
        spans.sort()

        for index, (start, kind) in enumerate(spans):
            end = spans[index + 1][0] if index + 1 < len(spans) else len(sentence)
            span = sentence[start:end]
            marker = _TARGET_MARKER_RE.search(span)
            search_area = span[marker.end() :] if marker else span
            names = [name for name in _candidates(search_area) if _plausible(name)]
            if not names and marker:
                # "converted it to webm" names no file: nothing to check.
                names = [name for name in _candidates(span) if _plausible(name)]
            if not names:
                continue
            raw = names[-1]
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = directory / candidate
            if kind == "created" and raw in before:
                continue  # a pre-existing input, not something this turn produced
            claims.append(Claim(kind=kind, path=candidate, raw=raw))
    return claims


# ---------------------------------------------------------------------------
# 5. The verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reasons: tuple[str, ...] = ()
    summary: str = ""
    checked: bool = False
    claims: int = 0

    def failure_message(self) -> str:
        if not self.reasons:
            return "Not done — the work could not be verified."
        return "Not done — " + " ".join(self.reasons)


def _dedupe(items: list[str]) -> list[str]:
    """A retried claim would otherwise be reported once per attempt."""
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


@dataclass
class TurnVerifier:
    """Tracks one user turn: the request, the tree before it, the commands run."""

    directory: Path
    request: str = ""
    intent: RequestIntent = field(default_factory=lambda: RequestIntent(False))
    _before: dict[str, FileStat] = field(default_factory=dict)
    _commands: list[str] = field(default_factory=list)
    _effects: list[CommandEffect] = field(default_factory=list)
    _codes: list[int] = field(default_factory=list)
    _inspected: bool = False

    def begin(self, request: str) -> None:
        self.request = request
        self.intent = classify_request(request)
        self._before = snapshot_directory(self.directory)
        self._commands = []
        self._effects = []
        self._codes = []
        self._inspected = False

    def record_inspection(self) -> None:
        """The agent looked at the directory (list_directory or a read command)."""
        self._inspected = True

    def record_command(self, command: str, returncode: int = 0) -> None:
        effect = analyze_command(command)
        self._commands.append(command)
        self._effects.append(effect)
        self._codes.append(returncode)
        if not effect.mutating:
            self._inspected = True

    def _display(self, path: Path) -> str:
        """Paths read better relative to the folder the user opened."""
        try:
            return str(path.relative_to(self.directory)) or "."
        except ValueError:
            return str(path)

    def mutating_ran(self) -> bool:
        return any(effect.mutating for effect in self._effects)

    def mutating_succeeded(self) -> bool:
        return any(
            effect.mutating and code == 0
            for effect, code in zip(self._effects, self._codes)
        )

    def _last_mutating(self, *, succeeded: bool) -> str:
        pairs = list(zip(self._commands, self._effects, self._codes))
        for command, effect, code in reversed(pairs):
            if effect.mutating and (code == 0) == succeeded:
                return command
        return pairs[-1][0] if pairs else ""

    def verdict(self, replies: list[str]) -> Verdict:
        before = self._before
        after = snapshot_directory(self.directory)
        effects = diff_snapshots(before, after)

        reasons: list[str] = []
        evidence: list[str] = []
        checked = False

        # (a) Declared outputs, straight from the commands that ran. A command
        # that exited non-zero is judged by its failure, not by its promises.
        succeeded = [
            effect for effect, code in zip(self._effects, self._codes) if code == 0
        ]
        declared: list[Path] = []
        for effect in succeeded:
            declared.extend(resolve_outputs(self.directory, effect.outputs))
        declared_ok = False
        for path in declared:
            checked = True
            try:
                present = path.exists()
                empty = present and path.is_file() and path.stat().st_size == 0
            except OSError:
                present, empty = False, False
            if not present:
                reasons.append(f"{self._display(path)} was never created.")
            elif empty:
                reasons.append(f"{self._display(path)} was created but is empty.")
            else:
                declared_ok = True
        if declared:
            evidence.append(
                "checked " + ", ".join(self._display(path) for path in declared[:3])
            )

        # (b) Claims in the reply.
        claims = extract_claims("\n".join(replies), self.directory, set(before))
        for claim in claims:
            checked = True
            try:
                present = claim.path.exists()
            except OSError:
                present = False
            if claim.kind == "created" and not present:
                reasons.append(
                    f"the reply says {claim.raw} was created, but it does not exist."
                )
            elif claim.kind == "deleted" and present:
                reasons.append(
                    f"the reply says {claim.raw} was removed, but it is still there."
                )

        # (b2) Deletions: the operand must be gone, wherever it lived.
        removed_ok = False
        for effect in succeeded:
            for raw in effect.removals:
                for path in resolve_outputs(self.directory, (raw,)):
                    checked = True
                    try:
                        present = path.exists()
                    except OSError:
                        present = True
                    if present:
                        reasons.append(
                            f"{self._display(path)} was supposed to be deleted "
                            "but it is still there."
                        )
                    else:
                        removed_ok = True

        # (c) Effects in the directory.
        if effects.changed:
            checked = True
            evidence.append(effects.describe())

        text = "\n".join(replies)
        if not effects.changed and not declared_ok and not removed_ok:
            # "delete the .tmp files" in a folder with none is a correct no-op:
            # the agent looked, found nothing, and said so.
            if self.intent.is_action and self._inspected and _NO_OP_RE.search(text):
                return Verdict(
                    ok=True,
                    summary="Nothing changed — the reply reports there was nothing to act on.",
                    checked=True,
                    claims=len(claims),
                )
            # A command that failed has already told the model what went wrong;
            # the turn is only left open so the next reply can fix it.
            if self.mutating_succeeded():
                checked = True
                reasons.append(
                    f"`{self._last_mutating(succeeded=True)}` reported success but "
                    f"nothing in {self.directory} was created, changed or deleted."
                )
            elif self.intent.is_action:
                checked = True
                if self.mutating_ran():
                    reasons.append(
                        f"`{self._last_mutating(succeeded=False)}` failed, and "
                        f"nothing in {self.directory} was created, changed or deleted."
                    )
                else:
                    reasons.append(
                        "no command that could carry out the request was run — "
                        "the reply only describes the work."
                    )

        reasons = _dedupe(reasons)
        if not reasons:
            summary = "Verified"
            if evidence:
                summary = "Verified: " + "; ".join(evidence[:2])
            return Verdict(ok=True, summary=summary, checked=checked, claims=len(claims))

        return Verdict(
            ok=False,
            reasons=tuple(reasons),
            summary="Not verified — " + " ".join(reasons),
            checked=True,
            claims=len(claims),
        )


# "delete the .tmp files" in a folder with none: a correct no-op, not a lie.
_NO_OP_RE = re.compile(
    r"\b(?:no (?:such|matching)|no (?:files?|items?|videos?|images?|archives?) (?:to|found|match)|"
    r"nothing to|nothing was (?:done|changed|found|needed)|no changes?|none found|"
    r"already (?:exists?|present)|"
    r"doesn't exist|does not exist|there (?:are|is) no)\b",
    re.IGNORECASE,
)

_COMPLETION_RE = re.compile(
    r"\b(?:done|finished|complete|completed|all set|successfully|succeeded|ready)\b",
    re.IGNORECASE,
)
_FAILURE_WORDS_RE = re.compile(
    r"\b(?:not installed|missing|failed|fail|cannot|can't|couldn't|could not|"
    r"unable|error|denied|no such|nothing to)\b",
    re.IGNORECASE,
)


def closing_is_a_claim(text: str, claim_count: int) -> bool:
    """True when an unverified closing reply must not be shown to the user.

    A failed turn still shows an explanation ("ffmpeg is not installed, so
    nothing was converted"), because that is the useful part. What it never
    shows is a success claim: a named output file, or a bare "Done".
    """
    if claim_count:
        return True
    if not _COMPLETION_RE.search(text):
        return False
    return not _FAILURE_WORDS_RE.search(text)


def correction_prompt(verdict: Verdict) -> str:
    """The message pushed back into the conversation when a check fails."""
    detail = "\n".join(f"- {reason}" for reason in verdict.reasons)
    return (
        "[InDir verification — automatic, not the user]\n"
        "This request is NOT done. The filesystem does not show the result:\n"
        f"{detail}\n\n"
        "Do not describe work you have not done. Either run the command that "
        "actually produces the result now, or state plainly that it failed and "
        "why (for example the required tool is missing)."
    )
