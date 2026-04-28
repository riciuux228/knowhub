"""Smart document chunking with breakpoint detection.

Ported from tobi/qmd (TypeScript) — breakpoint-based splitting with
code fence awareness and distance-decay scoring.
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

# Chunking defaults (matching QMD)
CHUNK_SIZE_TOKENS = 900
CHUNK_OVERLAP_TOKENS = int(CHUNK_SIZE_TOKENS * 0.15)  # 135 tokens
CHUNK_SIZE_CHARS = CHUNK_SIZE_TOKENS * 4  # ~3600 chars
CHUNK_OVERLAP_CHARS = CHUNK_OVERLAP_TOKENS * 4  # ~540 chars
CHUNK_WINDOW_CHARS = 200 * 4  # ~800 chars


@dataclass
class BreakPoint:
    pos: int  # character position
    score: float  # higher = better break point
    bp_type: str  # 'h1', 'h2', 'blank', etc.


@dataclass
class CodeFenceRegion:
    start: int
    end: int


# Break point patterns with scores (higher = better split point)
# Order matters — more specific patterns first
BREAK_PATTERNS: List[Tuple[re.Pattern, float, str]] = [
    (re.compile(r'\n#{1}(?!#)'), 100, 'h1'),
    (re.compile(r'\n#{2}(?!#)'), 90, 'h2'),
    (re.compile(r'\n#{3}(?!#)'), 80, 'h3'),
    (re.compile(r'\n#{4}(?!#)'), 70, 'h4'),
    (re.compile(r'\n#{5}(?!#)'), 60, 'h5'),
    (re.compile(r'\n#{6}(?!#)'), 50, 'h6'),
    (re.compile(r'\n```'), 80, 'codeblock'),
    (re.compile(r'\n(?:---|\*\*\*|___)\s*\n'), 60, 'hr'),
    (re.compile(r'\n\n+'), 20, 'blank'),
    (re.compile(r'\n[-*]\s'), 5, 'list'),
    (re.compile(r'\n\d+\.\s'), 5, 'numlist'),
    (re.compile(r'\n'), 1, 'newline'),
]


def scan_break_points(text: str) -> List[BreakPoint]:
    """Scan text for all potential break points with scores."""
    seen: dict[int, BreakPoint] = {}

    for pattern, score, bp_type in BREAK_PATTERNS:
        for m in pattern.finditer(text):
            pos = m.start()
            existing = seen.get(pos)
            if existing is None or score > existing.score:
                seen[pos] = BreakPoint(pos=pos, score=score, bp_type=bp_type)

    return sorted(seen.values(), key=lambda bp: bp.pos)


def find_code_fences(text: str) -> List[CodeFenceRegion]:
    """Find all code fence regions (between ``` markers)."""
    regions: List[CodeFenceRegion] = []
    fence_pattern = re.compile(r'\n```')
    in_fence = False
    fence_start = 0

    for m in fence_pattern.finditer(text):
        if not in_fence:
            fence_start = m.start()
            in_fence = True
        else:
            regions.append(CodeFenceRegion(start=fence_start, end=m.start() + len(m.group())))
            in_fence = False

    if in_fence:
        regions.append(CodeFenceRegion(start=fence_start, end=len(text)))

    return regions


def is_inside_code_fence(pos: int, fences: List[CodeFenceRegion]) -> bool:
    """Check if a position is inside a code fence."""
    return any(f.start < pos < f.end for f in fences)


def find_best_cutoff(
    break_points: List[BreakPoint],
    target_pos: int,
    window_chars: int = CHUNK_WINDOW_CHARS,
    decay_factor: float = 0.7,
    code_fences: Optional[List[CodeFenceRegion]] = None,
) -> int:
    """Find the best cut position using scored break points with distance decay."""
    if code_fences is None:
        code_fences = []

    window_start = target_pos - window_chars
    best_score = -1.0
    best_pos = target_pos

    for bp in break_points:
        if bp.pos < window_start:
            continue
        if bp.pos > target_pos:
            break
        if is_inside_code_fence(bp.pos, code_fences):
            continue

        # Distance decay: squared for gentler falloff
        distance = target_pos - bp.pos
        decay = max(0, 1.0 - (distance / window_chars) ** 2 * (1 - decay_factor))
        effective_score = bp.score * decay

        if effective_score > best_score:
            best_score = effective_score
            best_pos = bp.pos

    return best_pos


def chunk_document(
    text: str,
    max_chars: int = CHUNK_SIZE_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> List[dict]:
    """Split document into chunks using smart breakpoint detection.

    Returns list of {text, pos} dicts.
    """
    if not text or len(text) <= max_chars:
        return [{"text": text, "pos": 0}] if text else []

    break_points = scan_break_points(text)
    code_fences = find_code_fences(text)

    chunks: List[dict] = []
    char_pos = 0

    while char_pos < len(text):
        # Target end position
        target_end = char_pos + max_chars

        if target_end >= len(text):
            # Last chunk
            chunk_text = text[char_pos:].strip()
            if chunk_text:
                chunks.append({"text": chunk_text, "pos": char_pos})
            break

        # Find best cut position
        cut_pos = find_best_cutoff(break_points, target_end, code_fences=code_fences)

        # Ensure we make progress
        if cut_pos <= char_pos:
            cut_pos = target_end

        chunk_text = text[char_pos:cut_pos].strip()
        if chunk_text:
            chunks.append({"text": chunk_text, "pos": char_pos})

        # Next chunk starts with overlap
        char_pos = max(cut_pos - overlap_chars, char_pos + 1)

    return chunks
