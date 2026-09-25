"""Parse MCQ question files (Word .docx, CSV) into rows for the questions table.

Word format understood (the layout produced by most AI chat exports / question writers):

    QUESTION 1                      <- heading (also "Q1.", "Question 1:", "Q.1)")
    Consider the following ...      <- stem paragraphs, bullet/numbered statements, tables
    - statement one
    - statement two
    Which of the above is correct?
    A) 1 only                       <- options on separate lines or one paragraph with line breaks
    B) 2 only
    C) ...
    D) ...
    Answer: B) 2 only               <- only the letter is used
    Explanation:                    <- optional; paragraphs and bullets are kept

Only the standard library is used so no extra dependency is needed in production.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

MAX_QUESTIONS_PER_IMPORT = 1000


class ImportFileError(ValueError):
    """The uploaded file itself is unusable (not a docx, no questions, ...)."""


@dataclass
class ParsedQuestion:
    number: int
    stem: str = ""
    options: dict[str, str] = field(default_factory=dict)
    answer: str | None = None
    explanation: str = ""
    subject: str | None = None      # CSV only
    topic: str | None = None        # CSV only
    difficulty: str | None = None   # CSV only
    source: str | None = None       # CSV only
    year: int | None = None         # CSV only (UPSC exam year for previous-year questions)
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Word (.docx)
# --------------------------------------------------------------------------- #
@dataclass
class _Block:
    kind: str            # "p" | "list" | "table"
    lines: list[str]


def _clean(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def _paragraph_lines(p: ET.Element) -> list[str]:
    """Text of a w:p split on manual line breaks (Shift+Enter)."""
    lines: list[str] = [""]
    for el in p.iter():
        if el.tag == W + "t":
            lines[-1] += el.text or ""
        elif el.tag in (W + "tab", W + "noBreakHyphen"):
            lines[-1] += " " if el.tag == W + "tab" else "-"
        elif el.tag in (W + "br", W + "cr"):
            if el.get(W + "type") in (None, "textWrapping"):
                lines.append("")
    return [c for c in (_clean(x) for x in lines) if c]


_BULLET_PREFIX = re.compile(r"^[\u2022\u25cf\u25aa\u25e6\u2023\u2043\-\u2013\u2014*]\s+")


def _read_blocks(document_xml: bytes) -> list[_Block]:
    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:  # pragma: no cover - corrupt file
        raise ImportFileError("The Word file is damaged and could not be read.") from exc
    body = root.find(W + "body")
    if body is None:
        raise ImportFileError("The Word file has no readable content.")
    blocks: list[_Block] = []
    for child in body:
        if child.tag == W + "p":
            lines = _paragraph_lines(child)
            if not lines:
                continue
            num = child.find(f"{W}pPr/{W}numPr")
            numbered = num is not None and (num.find(W + "numId") is None or num.find(W + "numId").get(W + "val") != "0")
            first = lines[0]
            bulleted = bool(_BULLET_PREFIX.match(first))
            if bulleted:
                lines[0] = _BULLET_PREFIX.sub("", first)
            blocks.append(_Block("list" if (numbered or bulleted) else "p", lines))
        elif child.tag == W + "tbl":
            rows: list[str] = []
            for tr in child.iter(W + "tr"):
                cells = []
                for tc in tr.findall(W + "tc"):
                    parts = [" ".join(_paragraph_lines(p)) for p in tc.iter(W + "p")]
                    cells.append(_clean(" ".join(x for x in parts if x)))
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append(_Block("table", rows))
    return blocks


_QUESTION_START = re.compile(
    r"^\s*(?:question|ques|q)\s*\.?\s*(\d+)\s*(?:$|[.:)\-\u2013\u2014]+\s*(.*)$)", re.IGNORECASE
)
_OPTION_LINE = re.compile(r"^\(?([A-Da-d])[\).:]\s*(.*)$")
_INLINE_OPTIONS = re.compile(r"(?:^|\s)\(?([A-Da-d])[\).]\s+")
_ANSWER_LINE = re.compile(
    r"^\s*(?:correct\s+)?(?:answer|ans)\s*(?:option)?\s*[:\-\u2013]?\s*\(?([A-Za-z])\)?(?=$|[\s).:,\-\u2013])",
    re.IGNORECASE,
)
_EXPLANATION_LINE = re.compile(
    r"^\s*(?:explanation|solution|rationale)\s*(?:[:\-\u2013]\s*(.*)|$)", re.IGNORECASE
)
_LEADING_NUMBER = re.compile(r"^\(?\d+[\).]\s")


@dataclass
class _Item:
    block: int
    kind: str      # "p" | "list" | "table"
    text: str


def _split_inline_options(line: str) -> list[str] | None:
    """'A) x B) y C) z D) w' on one line -> ['A) x', 'B) y', ...]; None if not that shape."""
    hits = list(_INLINE_OPTIONS.finditer(line))
    letters = [h.group(1).upper() for h in hits]
    if len(hits) >= 3 and letters == list("ABCD")[: len(letters)]:
        return [
            line[h.start(): hits[i + 1].start() if i + 1 < len(hits) else len(line)].strip()
            for i, h in enumerate(hits)
        ]
    return None


def _split_questions(blocks: list[_Block]) -> list[tuple[int, list[_Block]]]:
    """Group blocks by 'QUESTION n' headings. A heading may also carry the first stem line."""
    groups: list[tuple[int, list[_Block]]] = []
    for block in blocks:
        m = _QUESTION_START.match(block.lines[0]) if block.kind == "p" else None
        if m:
            groups.append((int(m.group(1)), []))
            remaining = ([m.group(2).strip()] if m.group(2) and m.group(2).strip() else []) + block.lines[1:]
            if remaining:
                groups[-1][1].append(_Block("p", remaining))
        elif groups:
            groups[-1][1].append(block)
    return groups


def _render_stem(items: list[_Item]) -> str:
    """Paragraphs -> lines, bullet lists -> numbered statements, tables -> 'a | b' rows."""
    chunks: list[tuple[str, list[str]]] = []
    prev_block = None
    for it in items:
        same_group = chunks and (
            it.block == prev_block or (it.kind in ("list", "table") and chunks[-1][0] == it.kind)
        )
        if same_group and chunks[-1][0] == it.kind:
            chunks[-1][1].append(it.text)
        else:
            chunks.append((it.kind, [it.text]))
        prev_block = it.block
    rendered = []
    for kind, lines in chunks:
        if kind == "list":
            n, out = 0, []
            for ln in lines:
                if _LEADING_NUMBER.match(ln):
                    out.append(ln)
                else:
                    n += 1
                    out.append(f"{n}. {ln}")
            lines = out
        rendered.append("\n".join(lines))
    return "\n\n".join(rendered).strip()


def _parse_question(number: int, blocks: list[_Block]) -> ParsedQuestion:
    q = ParsedQuestion(number=number)

    # 1) flatten to lines; "A) x B) y C) z D) w" on one line becomes four lines
    items: list[_Item] = []
    for bi, block in enumerate(blocks):
        for line in block.lines:
            parts = _split_inline_options(line) if block.kind != "table" else None
            for part in parts or [line]:
                items.append(_Item(bi, "p" if parts else block.kind, part))

    def is_text(it: _Item) -> bool:
        return it.kind != "table"

    # 2) find the Answer / Explanation markers
    answer_idx = next((i for i, it in enumerate(items) if is_text(it) and _ANSWER_LINE.match(it.text)), None)
    expl_idx = next((i for i, it in enumerate(items) if is_text(it) and _EXPLANATION_LINE.match(it.text)), None)
    end_body = min(x for x in (answer_idx, expl_idx, len(items)) if x is not None)

    # 3) options = the last A..D run before the markers (so lettered statements in the stem are not mistaken)
    def opt_letter(it: _Item) -> str | None:
        m = _OPTION_LINE.match(it.text) if is_text(it) else None
        return m.group(1).upper() if m else None

    opt_start = None
    for j in range(end_body - 1, -1, -1):
        if opt_letter(items[j]) != "A":
            continue
        want, k = list("BCD"), j + 1
        while want and k < end_body:
            if opt_letter(items[k]) == want[0]:
                want.pop(0)
            k += 1
        if not want:
            opt_start = j
            break

    body_end_for_stem = opt_start if opt_start is not None else end_body
    q.stem = _render_stem(items[:body_end_for_stem])

    if opt_start is not None:
        current = None
        for it in items[opt_start:end_body]:
            letter = opt_letter(it)
            expected = "ABCD"[len(q.options)] if len(q.options) < 4 else None
            if letter and letter == expected:
                q.options[letter] = _OPTION_LINE.match(it.text).group(2).strip()
                current = letter
            elif current:
                q.options[current] = (q.options[current] + " " + it.text).strip()

    # 4) answer letter
    if answer_idx is not None:
        letter = _ANSWER_LINE.match(items[answer_idx].text).group(1).upper()
        if letter in "ABCD":
            q.answer = letter
        else:
            q.errors.append(f"answer '{letter}' is not one of A, B, C, D")

    # 5) explanation = everything after the first marker except the Answer line itself
    tail_start = end_body
    expl_lines: list[str] = []
    for i in range(tail_start, len(items)):
        it = items[i]
        if i == answer_idx:
            continue
        if i == expl_idx:
            inline = _EXPLANATION_LINE.match(it.text).group(1)
            if inline and inline.strip():
                expl_lines.append(inline.strip())
            continue
        expl_lines.append(("\u2022 " if it.kind == "list" else "") + it.text)
    q.explanation = "\n".join(expl_lines).strip()
    return q


def parse_docx(data: bytes) -> list[ParsedQuestion]:
    if not data[:2] == b"PK":
        raise ImportFileError("This is not a .docx file. Open it in Word and use Save As → Word Document (.docx).")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "word/document.xml" not in z.namelist():
                raise ImportFileError("This does not look like a Word (.docx) file.")
            document_xml = z.read("word/document.xml")
    except zipfile.BadZipFile as exc:
        raise ImportFileError("The Word file is damaged and could not be opened.") from exc
    groups = _split_questions(_read_blocks(document_xml))
    if not groups:
        raise ImportFileError(
            "No questions found. Start each question with a heading such as 'QUESTION 1' (or 'Q1.'), "
            "followed by the question, options A) to D), 'Answer: B' and an optional 'Explanation:'."
        )
    return [_finish(_parse_question(n, blocks)) for n, blocks in groups]


# --------------------------------------------------------------------------- #
# CSV (matches the sample template shown in the admin panel)
# --------------------------------------------------------------------------- #
_CSV_ALIASES = {
    "question": "stem", "stem": "stem", "question text": "stem",
    "a": "A", "option a": "A", "option_a": "A", "b": "B", "option b": "B", "option_b": "B",
    "c": "C", "option c": "C", "option_c": "C", "d": "D", "option d": "D", "option_d": "D",
    "answer": "answer", "correct": "answer", "correct option": "answer", "correct_option": "answer",
    "subject": "subject", "topic": "topic", "difficulty": "difficulty",
    "explanation": "explanation", "source": "source",
    "year": "year", "upsc year": "year", "upsc_year": "year", "exam year": "year",
}


def parse_csv(data: bytes) -> list[ParsedQuestion]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ImportFileError("The CSV file is empty.")
    cols = [_CSV_ALIASES.get(h.strip().lower(), None) for h in header]
    missing = [c for c in ("stem", "A", "B", "C", "D", "answer") if c not in cols]
    if missing:
        raise ImportFileError(
            "CSV is missing required column(s): " + ", ".join(missing)
            + ". Expected: Question, A, B, C, D, Answer (optional: Subject, Topic, Difficulty, Explanation, Source)."
        )
    out: list[ParsedQuestion] = []
    for i, row in enumerate(reader, start=1):
        if not any(cell.strip() for cell in row):
            continue
        rec = {c: _clean(v) if c != "explanation" else v.strip() for c, v in zip(cols, row) if c}
        q = ParsedQuestion(number=i)
        q.stem = rec.get("stem", "")
        q.options = {k: rec.get(k, "") for k in "ABCD" if rec.get(k)}
        ans = rec.get("answer", "").strip().upper()[:1]
        if ans and ans in "ABCD":
            q.answer = ans
        elif ans:
            q.errors.append(f"answer '{rec.get('answer')}' is not one of A, B, C, D")
        q.explanation = rec.get("explanation", "")
        q.subject, q.topic = rec.get("subject") or None, rec.get("topic") or None
        q.difficulty, q.source = rec.get("difficulty") or None, rec.get("source") or None
        if rec.get("year"):
            if re.fullmatch(r"\d{4}", rec["year"]):
                q.year = int(rec["year"])
            else:
                q.errors.append(f"year '{rec['year']}' is not a 4-digit year")
        out.append(_finish(q))
    if not out:
        raise ImportFileError("The CSV has a header row but no questions.")
    return out


def _finish(q: ParsedQuestion) -> ParsedQuestion:
    if not q.stem:
        q.errors.append("question text is missing")
    missing = [k for k in "ABCD" if not q.options.get(k)]
    if missing:
        q.errors.append("option(s) " + ", ".join(missing) + " missing")
    if q.answer is None and not any("answer" in e for e in q.errors):
        q.errors.append("no 'Answer:' line found")
    return q


def normalise_stem(stem: str) -> str:
    return re.sub(r"\s+", " ", stem).strip().lower()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "topic"
