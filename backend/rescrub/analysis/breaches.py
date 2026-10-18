"""Rule-based contamination analysis over the tracking data -> timestamped findings.

Reads the per-frame proximity features (from Tiger Cloud or the CLI's JSON lines), groups
frames into contact events and applies sterile-technique rules:

- unscrubbed person contacts a sterile surface             -> finding
- scrubbed person contacts a nonsterile surface            -> finding
- ...and then contacts something sterile with the same hand -> one chained finding
  ("glove touches side table, then forceps"), the classic contamination path

Every finding uses the website's `Finding` shape (web/lib/types.ts) with
`timestampSec`/`endSec` in seconds from the start of the recording.

    python -m rescrub.analysis.breaches --jsonl out/            # CLI output folder
    python -m rescrub.analysis.breaches --session <uuid> [--save] # from Tiger Cloud
"""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

MAX_FINDINGS = 200
MAX_EVIDENCE = 12

PART_NAMES = {
    "wrist": "wrist", "elbow": "forearm", "hand_wrist": "hand", "thumb_tip": "thumb", "index_tip": "index finger",
    "middle_tip": "middle finger", "ring_tip": "ring finger", "pinky_tip": "little finger",
}


@dataclass
class Params:
    merge_gap_sec: float = 0.75   # contacts closer than this are one event
    min_frames: int = 2           # ignore single-frame flickers
    chain_window_sec: float = 20  # nonsterile contact followed by sterile contact within this -> chained finding
    # MM-OR labels unscrubbed staff touching the patient as a breach; scrubbed staff work on the patient.
    unscrubbed_patient_is_breach: bool = True


@dataclass
class ContactEvent:
    person: str
    person_designation: str
    side: str            # "left" | "right" | ""
    surface_id: str
    surface_label: str
    designation: str     # of the surface
    start: float
    end: float
    frames: int = 0
    inside_frames: int = 0
    depth_gaps: list = field(default_factory=list)
    parts: set = field(default_factory=set)

    @property
    def hand(self) -> str:
        return {"both": "hands", "": "hand"}.get(self.side, f"{self.side} hand")


def _side(part: str) -> str:
    return {"l": "left", "r": "right"}.get(part.split("_", 1)[0], "")


def frame_interval(rows: list[dict]) -> float:
    """Typical time between analyzed frames (e.g. 0.04 s for 25 fps video, 1 s for MM-OR)."""
    times = sorted({round(float(r["video_sec"]), 4) for r in rows})
    if len(times) < 2:
        return 0.0
    diffs = sorted(b - a for a, b in zip(times, times[1:]))
    return diffs[len(diffs) // 2]


def group_events(rows: list[dict], people: dict[str, str], labels: dict[str, str], p: Params) -> list[ContactEvent]:
    """Merge candidate-contact frames into events per (person, hand, surface).

    Gaps and minimum length scale with the frame rate, so 1-frame-per-second
    recordings are handled like normal video.
    """
    dt = frame_interval(rows)
    merge_gap = max(p.merge_gap_sec, 1.5 * dt)
    min_frames = 1 if dt >= 0.5 else p.min_frames  # at <=2 fps one frame already spans most of a second
    open_events: dict[tuple, ContactEvent] = {}
    done: list[ContactEvent] = []
    for r in sorted((r for r in rows if r["candidate_contact"]), key=lambda r: r["video_sec"]):
        key = (r["person_label"], _side(r["part"]), r["surface_id"])
        t = float(r["video_sec"])
        ev = open_events.get(key)
        if ev and t - ev.end > merge_gap:
            done.append(ev)
            ev = None
        if ev is None:
            ev = ContactEvent(r["person_label"], people.get(r["person_label"], "sterile"), key[1], r["surface_id"],
                              labels.get(r["surface_id"], r["surface_id"]), r["designation"], t, t)
            open_events[key] = ev
        if t > ev.end or ev.frames == 0:
            ev.frames += 1
            ev.inside_frames += bool(r.get("inside_mask"))
        ev.end = t
        ev.parts.add(r["part"].split("_", 1)[1] if "_" in r["part"] else r["part"])
        if r.get("depth_gap") is not None:
            ev.depth_gaps.append(float(r["depth_gap"]))
    done.extend(open_events.values())
    return sorted((e for e in done if e.frames >= min_frames), key=lambda e: e.start)


def merge_hands(events: list[ContactEvent], gap: float) -> list[ContactEvent]:
    """Left- and right-hand events of one person on one surface that overlap in time become one."""
    out: list[ContactEvent] = []
    for e in sorted(events, key=lambda e: e.start):
        twin = next((o for o in out if o.person == e.person and o.surface_id == e.surface_id
                     and o.side != e.side and e.start <= o.end + gap), None)
        if twin is None:
            out.append(ContactEvent(**{**e.__dict__, "parts": set(e.parts), "depth_gaps": list(e.depth_gaps)}))
            continue
        twin.side = "both" if twin.side != e.side else twin.side
        twin.end = max(twin.end, e.end)
        twin.frames += e.frames
        twin.inside_frames += e.inside_frames
        twin.depth_gaps += e.depth_gaps
        twin.parts |= e.parts
    return out


def _uncertainty(events: list[ContactEvent]) -> dict:
    has_depth = all(e.depth_gaps for e in events)
    inside = sum(e.inside_frames for e in events) / max(1, sum(e.frames for e in events))
    if has_depth and inside >= 0.6:
        return {"level": "low", "notes": "The hand is inside the surface outline at a similar depth for most frames."}
    if has_depth or inside >= 0.6:
        return {"level": "medium", "notes": "Contact is likely but a single camera cannot fully separate touching "
                                            "from hovering."}
    return {"level": "high", "notes": "Based on image position only (no depth). The hand may be hovering in "
                                      "front of the surface."}


def _step(e: ContactEvent, text: str) -> dict:
    return {"label": e.surface_label, "designation": e.designation if e.designation in ("sterile", "nonsterile")
            else "unknown", "timestampSec": round(e.start, 2), "observation": text}


def _parts(e: ContactEvent) -> str:
    return ", ".join(sorted({PART_NAMES.get(x, x) for x in e.parts}))


def analyze(rows: list[dict], people: dict[str, str], labels: dict[str, str], session_id: str,
            p: Params | None = None) -> list[dict]:
    p = p or Params()
    events = group_events(rows, people, labels, p)
    merge_gap = max(p.merge_gap_sec, 1.5 * frame_interval(rows))
    findings: list[dict] = []
    used: set[int] = set()

    def add(start: float, end: float, title: str, objects: list[str], description: str, reason: str,
            evidence: list[dict], unc_events: list[ContactEvent]) -> None:
        findings.append({
            "sessionId": session_id, "eventId": "", "timestampSec": round(max(0.0, start), 2),
            "endSec": round(end, 2), "title": title, "objects": objects, "description": description,
            "reasonForReview": reason, "evidence": evidence, "uncertainty": _uncertainty(unc_events),
            "reviewStatus": "needs_review",
        })

    # 1) Scrubbed hand: nonsterile contact, then sterile contact with the same hand.
    for i, a in enumerate(events):
        if a.person_designation != "sterile" or a.designation != "nonsterile":
            continue
        nxt = next((j for j, b in enumerate(events) if j > i and b.person == a.person and b.side == a.side
                    and b.designation == "sterile" and 0 <= b.start - a.end <= p.chain_window_sec), None)
        if nxt is None:
            continue
        b = events[nxt]
        used.update({i, nxt})
        add(a.start, b.end, f"{a.person}'s {a.hand} touches {a.surface_label}, then {b.surface_label}",
            [a.surface_label, f"{a.person} {a.hand}", b.surface_label],
            f"{a.person} ({_parts(a)}) contacts the nonsterile {a.surface_label} at {a.start:.1f}s, then the "
            f"sterile {b.surface_label} at {b.start:.1f}s with the same hand.",
            "A sterile glove appears to touch a nonsterile surface and then a sterile one; the glove would carry "
            "contamination onto the sterile field.",
            [_step(a, f"{a.person}'s {a.hand} contacts {a.surface_label} for {a.end - a.start:.1f}s."),
             {"label": f"{a.person} {a.hand}", "designation": "sterile", "timestampSec": round(a.end, 2),
              "observation": "Hand leaves the nonsterile surface."},
             _step(b, f"Same hand contacts {b.surface_label}.")],
            [a, b])

    # 2) Single contacts that break asepsis on their own (both hands on one surface = one finding).
    for e in merge_hands([e for i, e in enumerate(events) if i not in used], merge_gap):
        breach_targets = ("sterile", "patient") if p.unscrubbed_patient_is_breach else ("sterile",)
        if e.person_designation == "nonsterile" and e.designation in breach_targets:
            add(e.start, e.end, f"Unscrubbed {e.person} touches {e.surface_label}",
                [f"{e.person} (unscrubbed)", e.surface_label],
                f"{e.person}, who is not scrubbed, contacts the {e.designation} {e.surface_label} ({_parts(e)}).",
                "Only scrubbed staff may touch the sterile field or the prepped patient; this may need to be "
                "re-prepped or replaced.",
                [{"label": f"{e.person} {e.hand}", "designation": "nonsterile", "timestampSec": round(e.start, 2),
                  "observation": "Unscrubbed hand reaches the sterile surface."},
                 _step(e, f"Contact for {e.end - e.start:.1f}s.")], [e])
        elif e.person_designation == "sterile" and e.designation == "nonsterile":
            verb = "touch" if e.side == "both" else "touches"
            add(e.start, e.end, f"{e.person}'s {e.hand} {verb} {e.surface_label}",
                [f"{e.person} {e.hand}", e.surface_label],
                f"{e.person} ({_parts(e)}) contacts the nonsterile {e.surface_label}.",
                "A scrubbed person's glove or sleeve is no longer sterile after this contact; it should be changed "
                "before touching the sterile field.",
                [_step(e, f"{e.person}'s {e.hand} contacts {e.surface_label} for {e.end - e.start:.1f}s.")],
                [e])

    findings.sort(key=lambda f: (f["timestampSec"], f["endSec"]))
    findings = findings[:MAX_FINDINGS]  # same caps as the website (web/lib/validate.ts)
    for f in findings:
        f["evidence"] = f["evidence"][:MAX_EVIDENCE]
    for n, f in enumerate(findings, 1):
        f["eventId"] = f"evt-{n}"
    return findings


# --- loaders ---------------------------------------------------------------

def load_jsonl(out_dir: Path) -> tuple[list[dict], dict[str, str], dict[str, str], str]:
    out_dir = Path(out_dir)
    summary = json.loads((out_dir / "summary.json").read_text())
    people = {p["person_label"]: p.get("designation", "sterile") for p in summary["people"]}
    labels = {}
    for line in (out_dir / "surfaces.jsonl").read_text().splitlines():
        s = json.loads(line)
        labels[s["surface_id"]] = s["label"]
    rows = [json.loads(line) for line in (out_dir / "proximity_events.jsonl").read_text().splitlines()]
    return rows, people, labels, summary["session_id"]


def load_db(pool, session_id: str) -> tuple[list[dict], dict[str, str], dict[str, str]]:
    from .. import db

    people = {p["person_label"]: p["designation"] for p in db.list_people(pool, session_id)}
    labels = {s["surface_id"]: s["label"] for s in db.list_surfaces(pool, session_id)}
    rows = db.list_proximity(pool, session_id, candidate_only=True, start=None, end=None, limit=2_000_000)
    return rows, people, labels


def analyze_session(pool, session_id: str, save: bool = True) -> list[dict]:
    from .. import db

    rows, people, labels = load_db(pool, session_id)
    findings = analyze(rows, people, labels, session_id)
    if save:
        db.replace_findings(pool, session_id, findings)
    return findings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--jsonl", type=Path, help="output folder of `python -m rescrub.pipeline.run`")
    src.add_argument("--session", help="session id in Tiger Cloud")
    ap.add_argument("--save", action="store_true", help="write findings to the database (with --session)")
    ap.add_argument("--out", type=Path, help="also write findings JSON here")
    args = ap.parse_args()

    if args.jsonl:
        rows, people, labels, sid = load_jsonl(args.jsonl)
        findings = analyze(rows, people, labels, sid)
    else:
        from .. import db
        from ..settings import get_settings

        pool = db.connect(get_settings().database_url)
        findings = analyze_session(pool, args.session, save=args.save)
    text = json.dumps(findings, indent=2)
    if args.out:
        args.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
