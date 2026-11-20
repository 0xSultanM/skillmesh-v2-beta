"""
Roadmap -> professional PDF (ReportLab), generated in memory.

Input is the same JSON document as the "Export JSON" file
(``format: "skillmesh.roadmap"``), so a roadmap re-imported into TutMesh can
also be printed. Every string is escaped before it reaches ReportLab's
mini-markup, and only http(s) links are emitted.
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

FORMAT = "skillmesh.roadmap"
MAX_STEPS = 60
MAX_RES = 6

INK = colors.HexColor("#161a2b")
MUTED = colors.HexColor("#5d6478")
BORDER = colors.HexColor("#e2e5ef")
PRIMARY = colors.HexColor("#4f46e5")
PRIMARY_SOFT = colors.HexColor("#eef0ff")
SURFACE = colors.HexColor("#f6f7fb")
DIFF = {
    "easy": (colors.HexColor("#15803d"), colors.HexColor("#e7f6ec"), "EASY"),
    "medium": (colors.HexColor("#b45309"), colors.HexColor("#fff4e5"), "MEDIUM"),
    "hard": (colors.HexColor("#b91c1c"), colors.HexColor("#fdecec"), "HARD"),
}
PHASE_COLORS = {"foundation": colors.HexColor("#0ea5e9"), "required": PRIMARY, "preferred": colors.HexColor("#a855f7")}

_PUNCT = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                        "•": "-", "…": "...", "→": "->", " ": " ", "⇄": "<->"})


class RoadmapPDFError(ValueError):
    pass


class CheckBox(Flowable):
    """An empty, printable tick box."""

    def __init__(self, size=3.6 * mm, color=None, weight=0.9):
        super().__init__()
        self.size, self.color, self.weight = size, color or colors.HexColor("#9aa1b5"), weight

    def wrap(self, *_):
        return self.size, self.size

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.weight)
        self.canv.roundRect(0, 0, self.size, self.size, 0.8, stroke=1, fill=0)


def _with_box(content, box=None, width=None, top_pad=1.5):
    """[checkbox | content] row so boxes stay aligned with wrapped text."""
    t = Table([[box or CheckBox(), content]], colWidths=[6 * mm, width])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (0, 0), top_pad),
                           ("TOPPADDING", (1, 0), (1, 0), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return t


def clean(value, limit: int = 400) -> str:
    """Plain text safe for ReportLab markup and the built-in Latin-1 fonts."""
    s = str(value if value is not None else "").translate(_PUNCT)
    s = unicodedata.normalize("NFKC", s)
    s = s.encode("latin-1", "ignore").decode("latin-1")
    s = re.sub(r"\s+", " ", s).strip()[:limit]
    return escape(s)


def safe_url(url) -> str | None:
    u = str(url or "").strip()
    if not re.match(r"^https?://[^\s\"'<>]+$", u) or len(u) > 1000:
        return None
    return escape(u, {'"': "&quot;"})


def _num(v, default=0, lo=0, hi=10_000):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


# ---------- styles ----------
S = {
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=24, leading=29, textColor=INK, spaceAfter=4),
    "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=12, leading=16, textColor=MUTED),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=INK, spaceBefore=10, spaceAfter=6),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK),
    "muted": ParagraphStyle("muted", fontName="Helvetica", fontSize=8.5, leading=12, textColor=MUTED),
    "step": ParagraphStyle("step", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=INK),
    "pill": ParagraphStyle("pill", fontName="Helvetica-Bold", fontSize=7.5, leading=9, alignment=TA_CENTER),
    "statv": ParagraphStyle("statv", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=PRIMARY, alignment=TA_CENTER),
    "statl": ParagraphStyle("statl", fontName="Helvetica", fontSize=8, leading=10, textColor=MUTED, alignment=TA_CENTER),
    "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10, textColor=INK),
    "cellc": ParagraphStyle("cellc", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=MUTED, alignment=TA_CENTER),
}


def _page(canvas, doc, meta):
    w, h = A4
    canvas.saveState()
    canvas.setFillColor(PRIMARY)
    canvas.rect(0, h - 16 * mm, w, 16 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(18 * mm, h - 10 * mm, "SkillMesh AI")
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(w - 18 * mm, h - 10 * mm, meta["header"])
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(18 * mm, 10 * mm, f"Generated {meta['date']}  -  Track progress at /tutmesh/ by importing the JSON export")
    canvas.drawRightString(w - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _stat_tiles(stats):
    cells = [[Paragraph(clean(v), S["statv"]) for v, _ in stats], [Paragraph(clean(l), S["statl"]) for _, l in stats]]
    t = Table(cells, colWidths=[43 * mm] * len(stats))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PRIMARY_SOFT),
        ("LINEAFTER", (0, 0), (-2, -1), 3, colors.white),
        ("TOPPADDING", (0, 0), (-1, 0), 10), ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _streak_tracker(steps, weeks):
    header = [Paragraph("WK", S["cellc"]), Paragraph("FOCUS", S["label"])] + \
             [Paragraph(d, S["cellc"]) for d in ("M", "T", "W", "T", "F", "S", "S")] + [Paragraph("HOURS", S["cellc"])]
    rows = [header]
    for wk in range(1, weeks + 1):
        focus = [s["name"] for s in steps if s["start_week"] <= wk <= s["end_week"]]
        rows.append([Paragraph(str(wk), S["cellc"]), Paragraph(clean(", ".join(focus) or "Review & build", 90), S["cell"])]
                    + [CheckBox(4 * mm) for _ in range(7)] + [Paragraph("____", S["cellc"])])
    t = Table(rows, colWidths=[11 * mm, 67 * mm] + [9 * mm] * 7 + [17 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SURFACE),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfbfe")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (2, 1), (8, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _resource_lines(items):
    out = []
    for r in (items or [])[:MAX_RES]:
        if not isinstance(r, dict):
            continue
        url = safe_url(r.get("url"))
        title = clean(r.get("title"), 140) or "Resource"
        kind = clean(r.get("kind_label") or r.get("kind"), 30)
        by = clean(r.get("author"), 60)
        hours = r.get("est_hours")
        meta = " - ".join(x for x in (kind, by, f"~{hours:g} h" if isinstance(hours, (int, float)) and hours else "") if x)
        link = f'<link href="{url}" color="#4f46e5"><u>{title}</u></link>' if url else title
        parts = [Paragraph(f'{link} <font color="#5d6478" size="7.5">{meta}</font>', S["body"])]
        desc = clean(r.get("description"), 260)
        if desc:
            parts.append(Paragraph(desc, ParagraphStyle("rd", parent=S["muted"], spaceAfter=3)))
        out.append(_with_box(parts, CheckBox(3.2 * mm), 134 * mm, top_pad=2))
    return out


def _step_card(n, s):
    fg, bg, label = DIFF.get(s["difficulty"], DIFF["medium"])
    pill = Table([[Paragraph(f'<font color="{fg.hexval().replace("0x", "#")}">{label}</font>', S["pill"])]],
                 colWidths=[20 * mm], rowHeights=[6 * mm])
    pill.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("ROUNDEDCORNERS", [6, 6, 6, 6])]))
    weeks = f"week {s['start_week']}" + (f"-{s['end_week']}" if s["end_week"] > s["start_week"] else "")
    head = Table([[_with_box(Paragraph(f"{n}. {clean(s['name'], 80)}", S["step"]),
                             CheckBox(4.2 * mm, PRIMARY, 1.2), 126 * mm, top_pad=2.5), pill]],
                 colWidths=[134 * mm, 22 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    body = [head,
            Paragraph(f"{clean(s['reason'], 140)}  -  ~{s['hours']:g} h  -  {weeks}", S["muted"]),
            Spacer(1, 4),
            Paragraph(f'<b>Prove it:</b> {clean(s["project"], 300)}', S["body"])]
    if s.get("topics"):
        cells = [_with_box(Paragraph(clean(t, 100), S["cell"]), CheckBox(3 * mm), 70 * mm, top_pad=1) for t in s["topics"]]
        rows = [cells[i:i + 2] + ([""] if len(cells[i:i + 2]) == 1 else []) for i in range(0, len(cells), 2)]
        grid = Table(rows, colWidths=[76 * mm, 76 * mm])
        grid.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        body += [Spacer(1, 5), Paragraph("ESSENTIAL TOPICS (FROM COURSE SYLLABI)", S["label"]), grid]
    for key, title in (("video", "VIDEO TUTORIALS"), ("content", "BLOGS & CONTENT")):
        lines = _resource_lines((s.get("resources") or {}).get(key))
        if lines:
            body += [Spacer(1, 5), Paragraph(title, S["label"]), *lines]
    card = Table([[body]], colWidths=[160 * mm])
    card.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, fg),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return card


def _normalize(doc: dict) -> dict:
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise RoadmapPDFError("Not a SkillMesh roadmap export.")
    rm = doc.get("roadmap")
    if not isinstance(rm, dict) or not isinstance(rm.get("phases"), list):
        raise RoadmapPDFError("Roadmap is missing phases.")
    phases, count = [], 0
    for ph in rm["phases"][:3]:
        if not isinstance(ph, dict):
            continue
        steps = []
        for st in (ph.get("steps") or []):
            if not isinstance(st, dict) or count >= MAX_STEPS:
                continue
            count += 1
            res = st.get("resources") if isinstance(st.get("resources"), dict) else {}
            steps.append({
                "name": st.get("name") or "Skill", "reason": st.get("reason") or "",
                "hours": _num(st.get("hours"), 10, 0, 500), "project": st.get("project") or "",
                "difficulty": st.get("difficulty") if st.get("difficulty") in DIFF else "medium",
                "start_week": int(_num(st.get("start_week"), 1, 1, 200)),
                "end_week": int(_num(st.get("end_week"), 1, 1, 200)),
                "resources": {"video": res.get("video") or [], "content": res.get("content") or []},
                "topics": [str(t.get("name", ""))[:100] for t in ((res.get("topics") or {}).get("topics") or [])
                           if isinstance(t, dict) and t.get("name")][:12] if isinstance(res.get("topics"), dict) else [],
            })
        if steps:
            phases.append({"key": ph.get("key"), "title": ph.get("title") or "Phase",
                           "description": ph.get("description") or "", "steps": steps,
                           "weeks": ph.get("weeks") if isinstance(ph.get("weeks"), list) else [1, 1],
                           "hours": _num(ph.get("hours"), 0, 0, 5000)})
    if not phases:
        raise RoadmapPDFError("This roadmap has no steps.")
    return {"job": doc.get("job") if isinstance(doc.get("job"), dict) else {},
            "summary": doc.get("summary") if isinstance(doc.get("summary"), dict) else {},
            "rm": rm, "phases": phases}


def build_roadmap_pdf(doc: dict) -> tuple[bytes, str]:
    d = _normalize(doc)
    job, summary, rm, phases = d["job"], d["summary"], d["rm"], d["phases"]
    all_steps = [s for ph in phases for s in ph["steps"]]
    total_weeks = int(_num(rm.get("total_weeks"), max(s["end_week"] for s in all_steps), 1, 200))
    total_hours = _num(rm.get("total_hours"), sum(s["hours"] for s in all_steps), 0, 10000)
    hpw = _num(rm.get("hours_per_week"), 10, 1, 80)
    job_line = " at ".join(x for x in (clean(job.get("title"), 120), clean(job.get("company"), 80)) if x) or "Your target role"

    buf = io.BytesIO()
    meta = {"header": "Learning Roadmap", "date": date.today().strftime("%d %b %Y")}
    docu = BaseDocTemplate(buf, pagesize=A4, leftMargin=25 * mm, rightMargin=25 * mm, topMargin=26 * mm,
                           bottomMargin=20 * mm, title=f"Learning Roadmap - {job_line}", author="SkillMesh AI")
    frame = Frame(docu.leftMargin, docu.bottomMargin, docu.width, docu.height, id="f")
    docu.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=lambda c, dd: _page(c, dd, meta))])

    story = [
        Paragraph("Your Learning Roadmap", S["h1"]),
        Paragraph(f"Target role: <b>{job_line}</b>", S["sub"]),
        Spacer(1, 10),
    ]
    score = summary.get("score")
    stats = [
        (f"{int(_num(score, 0, 0, 100))}/100" if score is not None else "-", "CURRENT FIT SCORE"),
        (str(len(all_steps)), "SKILLS TO LEARN"),
        (f"{total_hours:g} h", "TOTAL STUDY TIME"),
        (f"{total_weeks} wk", f"AT {hpw:g} H / WEEK"),
    ]
    story += [_stat_tiles(stats), Spacer(1, 8)]
    if summary.get("verdict"):
        story.append(Paragraph(f"<b>Verdict:</b> {clean(summary.get('verdict'), 60)}. Follow the phases in order: "
                               "each skill builds on the ones before it.", S["body"]))

    story += [Spacer(1, 6), Paragraph("Overview", S["h2"])]
    ov = [[Paragraph("PHASE", S["label"]), Paragraph("SKILLS", S["label"]), Paragraph("WEEKS", S["label"]),
           Paragraph("HOURS", S["label"])]]
    for ph in phases:
        w = ph["weeks"]
        ov.append([Paragraph(f"<b>{clean(ph['title'], 60)}</b>", S["body"]),
                   Paragraph(clean(", ".join(s["name"] for s in ph["steps"]), 300), S["body"]),
                   Paragraph(clean(f"{w[0]}-{w[-1]}"), S["body"]), Paragraph(f"{ph['hours']:g}", S["body"])])
    ovt = Table(ov, colWidths=[42 * mm, 80 * mm, 18 * mm, 18 * mm])
    ovt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("BACKGROUND", (0, 0), (-1, 0), SURFACE), ("TOPPADDING", (0, 0), (-1, -1), 5),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story.append(ovt)

    story += [Spacer(1, 6), Paragraph("Streak tracker", S["h2"]),
              Paragraph("Tick a box for every day you study, even 20 minutes counts. Don't break the chain; "
                        "log your hours at the end of each week.", S["muted"]), Spacer(1, 6),
              _streak_tracker(all_steps, min(total_weeks, 26))]

    n = 0
    for ph in phases:
        story.append(PageBreak())
        color = PHASE_COLORS.get(ph["key"], PRIMARY)
        w = ph["weeks"]
        band = Table([[Paragraph(f'<font color="white"><b>{clean(ph["title"], 80)}</b></font>',
                                 ParagraphStyle("pb", parent=S["h2"], textColor=colors.white, spaceBefore=0, spaceAfter=0)),
                       Paragraph(f'<font color="white">Weeks {clean(w[0])}-{clean(w[-1])} - {ph["hours"]:g} h</font>',
                                 ParagraphStyle("pw", parent=S["body"], alignment=2))]],
                     colWidths=[110 * mm, 50 * mm])
        band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                  ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10)]))
        story += [band, Spacer(1, 4), Paragraph(clean(ph["description"], 200), S["muted"]), Spacer(1, 8)]
        for s in ph["steps"]:
            n += 1
            story += [KeepTogether([_step_card(n, s)]), Spacer(1, 8)]

    story += [Spacer(1, 6), Paragraph("Keep the streak alive", S["h2"])]
    for tip in ("Schedule fixed study slots in your calendar, as you would a meeting.",
                "Finish each step with its 'Prove it' project and push it to GitHub. Recruiters can verify it.",
                "Re-run your SkillMesh analysis after each phase to watch your fit score climb.",
                "Import the JSON export into TutMesh to track progress and streaks in your browser."):
        story.append(Paragraph(f"-&nbsp;&nbsp;{clean(tip)}", S["body"]))

    docu.build(story)
    slug = re.sub(r"[^a-z0-9]+", "-", (job.get("title") or "roadmap").lower()).strip("-")[:40] or "roadmap"
    return buf.getvalue(), f"skillmesh-roadmap-{slug}.pdf"
