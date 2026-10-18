import json

import pytest

from rescrub.analysis.breaches import Params, analyze, group_events
from training.evaluate_breaches import gt_events, match, timestamp_problems


def row(t, person, part, surface, designation, contact=True):
    return {"video_sec": t, "person_label": person, "part": part, "surface_id": surface, "designation": designation,
            "candidate_contact": contact, "inside_mask": True, "depth_gap": None}


LABELS = {"side-table-1": "Side table", "tray-1": "Instrument tray", "drape-1": "Sterile drape",
          "patient-1": "Patient"}


def test_frames_merge_into_events_with_exact_times():
    rows = [row(t / 10, "Person 1", "r_wrist", "side-table-1", "nonsterile") for t in range(20, 31)]
    rows += [row(t / 10, "Person 1", "r_index_tip", "side-table-1", "nonsterile") for t in range(20, 31)]
    rows += [row(5.0, "Person 1", "r_wrist", "side-table-1", "nonsterile")]  # single-frame flicker
    ev = group_events(rows, {"Person 1": "sterile"}, LABELS, Params())
    assert len(ev) == 1
    assert (ev[0].start, ev[0].end, ev[0].frames, ev[0].side) == (2.0, 3.0, 11, "right")


def test_chain_nonsterile_then_sterile_same_hand():
    rows = [row(t / 10, "Person 1", "r_wrist", "side-table-1", "nonsterile") for t in range(100, 111)]
    rows += [row(t / 10, "Person 1", "r_wrist", "tray-1", "sterile") for t in range(150, 156)]
    rows += [row(t / 10, "Person 1", "l_wrist", "drape-1", "sterile") for t in range(0, 300)]  # other hand
    f = analyze(rows, {"Person 1": "sterile"}, LABELS, "s1")
    assert len(f) == 1
    assert f[0]["timestampSec"] == 10.0 and f[0]["endSec"] == 15.5
    assert [e["timestampSec"] for e in f[0]["evidence"]] == [10.0, 11.0, 15.0]
    assert [e["designation"] for e in f[0]["evidence"]] == ["nonsterile", "sterile", "sterile"]
    assert timestamp_problems(f, 30.0) == []


def test_unscrubbed_touching_sterile_and_patient_rules():
    rows = [row(t / 10, "Person 2", "l_wrist", "tray-1", "sterile") for t in range(40, 45)]
    rows += [row(t / 10, "Person 1", "r_wrist", "patient-1", "patient") for t in range(0, 50)]
    f = analyze(rows, {"Person 1": "sterile", "Person 2": "nonsterile"}, LABELS, "s1")
    assert [x["title"] for x in f] == ["Unscrubbed Person 2 touches Instrument tray"]
    assert f[0]["eventId"] == "evt-1" and f[0]["timestampSec"] == 4.0 and f[0]["endSec"] == 4.4
    assert f[0]["reviewStatus"] == "needs_review" and f[0]["sessionId"] == "s1"


def test_sterile_contacts_by_scrubbed_staff_are_not_findings():
    rows = [row(t / 10, "Person 1", "r_wrist", "tray-1", "sterile") for t in range(0, 50)]
    assert analyze(rows, {"Person 1": "sterile"}, LABELS, "s1") == []


def test_gt_events_and_matching_on_video_clock():
    labels = {"000010": [["circulator", "instrument_table", "touching"]],
              "000011": [["circulator", "instrument_table", "touching"]],
              "000040": [["nurse", "operating_table", "touching"]], "000020": []}
    label_to_sec = {i: i * 0.5 for i in range(0, 60)}  # labeled frame i at 0.5 s * i
    ev = gt_events(labels, label_to_sec, merge_gap_sec=2)
    assert [(e["start"], e["end"]) for e in ev] == [(5.0, 5.5), (20.0, 20.0)]
    assert ev[0]["breaches"] == {"circulator touching instrument_table"}
    findings = [{"eventId": "evt-1", "timestampSec": 6.0, "endSec": 7.0},
                {"eventId": "evt-2", "timestampSec": 40.0, "endSec": 41.0}]
    tp, found, onset = match(findings, ev, tolerance=2.0)
    assert (tp, found) == (1, 1) and onset == [pytest.approx(1.0)]


def test_matching_is_one_to_one():
    ev = [{"start": 10.0, "end": 11.0}, {"start": 14.0, "end": 15.0}]
    dupes = [{"eventId": "evt-1", "timestampSec": 10.0}, {"eventId": "evt-2", "timestampSec": 10.5}]
    assert match(dupes, ev[:1], tolerance=1.0)[:2] == (1, 1)  # the duplicate is a false positive
    broad = [{"eventId": "evt-1", "timestampSec": 9.0, "endSec": 16.0}]
    assert match(broad, ev, tolerance=0.0)[:2] == (1, 1)  # one finding can't find two breaches


def test_timestamp_problems_detected():
    bad = [{"eventId": "evt-1", "timestampSec": 5, "endSec": 4, "evidence": [{"timestampSec": 9}]},
           {"eventId": "evt-2", "timestampSec": 2, "endSec": 99}]
    probs = timestamp_problems(bad, 30)
    assert any("ends before" in p for p in probs) and any("outside 5-4" in p for p in probs)
    assert any("not sorted" in p for p in probs) and any("outside the video" in p for p in probs)
    json.dumps(probs)


def test_both_hands_on_one_surface_is_one_finding():
    rows = [row(t / 10, "Person 1", side + "_wrist", "side-table-1", "nonsterile")
            for side in ("l", "r") for t in range(20, 40)]
    f = analyze(rows, {"Person 1": "sterile"}, LABELS, "s1")
    assert [x["title"] for x in f] == ["Person 1's hands touch Side table"]
    assert (f[0]["timestampSec"], f[0]["endSec"]) == (2.0, 3.9)


def test_one_frame_per_second_recordings_group_correctly():
    # MM-OR is captured at 1 frame/s: consecutive frames are 1 s apart and still one contact.
    rows = [row(float(t), "Person 2", "r_wrist", "tray-1", "sterile") for t in (10, 11, 12)]
    rows += [row(float(t), "Person 2", "r_wrist", "tray-1", "sterile") for t in (30,)]
    rows += [row(float(t), "Person 1", "l_elbow", "drape-1", "sterile") for t in range(0, 60)]  # sets the clock
    f = analyze(rows, {"Person 1": "sterile", "Person 2": "nonsterile"}, LABELS, "s1")
    assert [(x["timestampSec"], x["endSec"]) for x in f] == [(10.0, 12.0), (30.0, 30.0)]


def test_unscrubbed_touching_patient_is_breach_but_robot_is_neutral():
    labels = {**LABELS, "robot-1": "Robot"}
    rows = [row(t / 10, "Person 3", "r_wrist", "patient-1", "patient") for t in range(10, 20)]
    rows += [row(t / 10, "Person 3", "l_wrist", "robot-1", "neutral") for t in range(30, 40)]
    rows += [row(t / 10, "Person 1", "r_wrist", "robot-1", "neutral") for t in range(30, 40)]
    f = analyze(rows, {"Person 1": "sterile", "Person 3": "nonsterile"}, labels, "s1")
    assert [x["title"] for x in f] == ["Unscrubbed Person 3 touches Patient"]
