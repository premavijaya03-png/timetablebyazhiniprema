"""
app.py — Flask web backend for the Automatic Timetable Generator.

This is the web version of the original Tkinter desktop app. It reuses
ga_engine.py (the untouched GA/scheduling logic extracted from the
desktop app) and exposes it as a small JSON API + one HTML page.

Run locally (PC only):
    pip install flask --break-system-packages   (if not already installed)
    python app.py
Then open http://127.0.0.1:5000 in a browser.

Run so your PHONE can reach it too (same WiFi as this PC):
    Just double-click Start_Timetable_App.bat in this folder (Windows).
    It starts the server, shows this PC's WiFi (LAN) IP address, and
    opens the app on the PC automatically. Type that same address into
    your phone's browser, e.g. http://192.168.1.7:5000 - see README.md
    for the one-time Windows Firewall step this needs.
"""
import csv
import io
import os
from datetime import datetime

from flask import Flask, jsonify, render_template, request

import ga_engine

app = Flask(__name__)

# ----------------------------------------------------------------
# Settings / CSV management (Manage Faculty, Manage Course, Manage
# Dates screens). Each entry describes one editable CSV file: which
# columns it has (and their order on disk), and which columns are
# REQUIRED for a row to be kept (rows missing a required value are
# silently dropped, same as ga_engine already does when reading).
# ----------------------------------------------------------------
CSV_TABLES = {
    "faculty": {
        "filename": "faculty.csv",
        "columns": ["FacultyName"],
        "required": ["FacultyName"],
    },
    "courses": {
        "filename": "courses.csv",
        "columns": ["Shift", "Department", "Semester", "Class", "Course", "Faculty", "Hours"],
        "required": ["Shift", "Department", "Semester", "Class", "Course"],
    },
    "semester_dates": {
        "filename": "semester_dates.csv",
        "columns": ["Semester", "StartDate", "EndDate"],
        "required": ["Semester", "StartDate", "EndDate"],
    },
    "exam_dates": {
        "filename": "exam_dates.csv",
        "columns": ["ExamType", "Semester", "DayOrder", "Date", "Day"],
        "required": ["ExamType", "Semester", "DayOrder", "Date"],
    },
}


def _csv_path(table):
    return ga_engine._data_path(CSV_TABLES[table]["filename"])


@app.route("/api/settings/options")
def api_settings_options():
    """Dropdown options used by the Manage Course / Manage Dates
    'Add' forms on the Settings screens."""
    return jsonify({
        "shifts": ga_engine.SHIFT_LABELS,
        "departments": ga_engine.DEPARTMENTS,
        "classes_by_shift": ga_engine.SHIFT_CLASS_LIST,
        "semesters": ["I", "II", "III", "IV", "V", "VI"],
        "terms": ["ODD", "EVEN"],
        "day_orders": ga_engine.DAYS,
        "exam_types": ga_engine.EXAM_TYPES,
    })


@app.route("/api/settings/<table>", methods=["GET"])
def api_settings_get(table):
    if table not in CSV_TABLES:
        return jsonify({"error": f"Unknown table '{table}'"}), 404
    cfg = CSV_TABLES[table]
    rows = []
    path = _csv_path(table)
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({col: (row.get(col) or "").strip() for col in cfg["columns"]})
    return jsonify({"columns": cfg["columns"], "rows": rows})


@app.route("/api/settings/<table>", methods=["POST"])
def api_settings_save(table):
    if table not in CSV_TABLES:
        return jsonify({"error": f"Unknown table '{table}'"}), 404
    cfg = CSV_TABLES[table]
    body = request.get_json(silent=True) or {}
    incoming_rows = body.get("rows")
    if not isinstance(incoming_rows, list):
        return jsonify({"error": "'rows' must be a list"}), 400

    cleaned = []
    for row in incoming_rows:
        if not isinstance(row, dict):
            continue
        clean_row = {col: (row.get(col) or "").strip() for col in cfg["columns"]}
        if all(not clean_row[col] for col in cfg["required"]):
            continue  # fully-blank row, drop silently
        if any(not clean_row[col] for col in cfg["required"]):
            missing = [col for col in cfg["required"] if not clean_row[col]]
            return jsonify({"error": f"Row is missing required value(s): {', '.join(missing)}"}), 400
        cleaned.append(clean_row)

    if table == "semester_dates":
        sems = {r["Semester"].strip().upper() for r in cleaned}
        if sems != {"ODD", "EVEN"}:
            return jsonify({"error": "semester_dates must have exactly one ODD row and one EVEN row"}), 400
        for r in cleaned:
            for key in ("StartDate", "EndDate"):
                try:
                    datetime.strptime(r[key], "%d-%m-%Y")
                except ValueError:
                    return jsonify({"error": f"'{r[key]}' is not a valid date (use DD-MM-YYYY)"}), 400

    if table == "exam_dates":
        for r in cleaned:
            r["Semester"] = r["Semester"].strip().upper()
            if r["Semester"] not in ("ODD", "EVEN"):
                return jsonify({"error": f"Semester must be ODD or EVEN, got '{r['Semester']}'"}), 400

    # Write the file, then try to reload it into ga_engine. If the new
    # data is bad in a way we didn't already catch above, restore the
    # previous file so the running app never ends up broken.
    path = _csv_path(table)
    backup = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            backup = f.read()

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cfg["columns"])
    writer.writeheader()
    for row in cleaned:
        writer.writerow(row)
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(buf.getvalue())

    try:
        ga_engine.reload_data()
    except Exception as e:
        if backup is not None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(backup)
            ga_engine.reload_data()
        return jsonify({"error": f"Could not save: {e}"}), 400

    return jsonify({"ok": True, "rows": cleaned})


@app.before_request
def _refresh_data_from_csv():
    """Re-reads all the CSV files before handling any request, so edits
    made to courses.csv / faculty.csv / semester_dates.csv / etc. while
    the server is running are picked up immediately - no server restart
    needed. See ga_engine.reload_data() for why this is necessary."""
    ga_engine.reload_data()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/meta")
def api_meta():
    """Dropdown options for the page: departments, shifts, terms."""
    return jsonify({
        "departments": ga_engine.DEPARTMENTS,
        "shifts": ga_engine.SHIFT_LABELS,
        "terms": ["ODD", "EVEN"],
        "lab_combined_label": ga_engine.LAB_COMBINED_LABEL,
    })


def _validate(shift, term):
    if shift not in ga_engine.SHIFT_LABELS:
        return f"Unknown shift '{shift}'"
    if term not in ("ODD", "EVEN"):
        return f"Unknown term '{term}'"
    return None


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Runs the GA and returns one shift's full timetable (all its
    classes) as JSON, ready for the frontend to render as a table.

    Body (JSON, all optional):
        { "shift": "Shift 1", "department": "Computer Science", "term": "ODD" }
    """
    body = request.get_json(silent=True) or {}
    shift = body.get("shift") or ga_engine.SHIFT_LABELS[0]
    dept = body.get("department") or (ga_engine.DEPARTMENTS[0] if ga_engine.DEPARTMENTS else "")
    term = body.get("term") or "ODD"
    shared_lab = bool(body.get("shared_lab", False))

    err = _validate(shift, term)
    if err:
        return jsonify({"error": err}), 400

    result = ga_engine.generate_shift_timetable(shift, dept, term, shared_lab=shared_lab)
    return jsonify(result)


@app.route("/api/all", methods=["POST"])
def api_all():
    body = request.get_json(silent=True) or {}
    shift = body.get("shift") or ga_engine.SHIFT_LABELS[0]
    dept = body.get("department") or (ga_engine.DEPARTMENTS[0] if ga_engine.DEPARTMENTS else "")
    term = body.get("term") or "ODD"
    shared_lab = bool(body.get("shared_lab", False))
    err = _validate(shift, term)
    if err:
        return jsonify({"error": err}), 400
    return jsonify(ga_engine.generate_all_timetable(shift, dept, term, shared_lab=shared_lab))


@app.route("/api/lab", methods=["POST"])
def api_lab():
    """Body: { "shift", "department", "term", "shared_lab" }
    `shift` can also be ga_engine.LAB_COMBINED_LABEL ("Shift 1 & Shift 2")
    - in that case both shifts' Lab Timetables are generated and
    returned together (use /api/lab-combined directly for the same
    result with clearer field names)."""
    body = request.get_json(silent=True) or {}
    shift = body.get("shift") or ga_engine.SHIFT_LABELS[0]
    dept = body.get("department") or (ga_engine.DEPARTMENTS[0] if ga_engine.DEPARTMENTS else "")
    term = body.get("term") or "ODD"
    shared_lab = bool(body.get("shared_lab", False))

    if shift == ga_engine.LAB_COMBINED_LABEL:
        return jsonify(ga_engine.generate_lab_timetable_combined(dept, term, shared_lab=shared_lab))

    err = _validate(shift, term)
    if err:
        return jsonify({"error": err}), 400
    return jsonify(ga_engine.generate_lab_timetable(shift, dept, term, shared_lab=shared_lab))


@app.route("/api/lab-combined", methods=["POST"])
def api_lab_combined():
    """Shift 1's and Shift 2's Lab Timetables together in one response,
    for the "Shift 1 & Shift 2" combined print/view option.
    Body: { "department", "term", "shared_lab" }
    """
    body = request.get_json(silent=True) or {}
    dept = body.get("department") or (ga_engine.DEPARTMENTS[0] if ga_engine.DEPARTMENTS else "")
    term = body.get("term") or "ODD"
    shared_lab = bool(body.get("shared_lab", False))
    if term not in ("ODD", "EVEN"):
        return jsonify({"error": f"Unknown term '{term}'"}), 400
    return jsonify(ga_engine.generate_lab_timetable_combined(dept, term, shared_lab=shared_lab))


@app.route("/api/class-list", methods=["POST"])
def api_class_list():
    body = request.get_json(silent=True) or {}
    shift = body.get("shift") or ga_engine.SHIFT_LABELS[0]
    return jsonify({"classes": ga_engine.list_classes_for_shift(shift)})


@app.route("/api/student", methods=["POST"])
def api_student():
    body = request.get_json(silent=True) or {}
    shift = body.get("shift") or ga_engine.SHIFT_LABELS[0]
    dept = body.get("department") or (ga_engine.DEPARTMENTS[0] if ga_engine.DEPARTMENTS else "")
    term = body.get("term") or "ODD"
    class_name = body.get("class_name")
    shared_lab = bool(body.get("shared_lab", False))
    err = _validate(shift, term)
    if err:
        return jsonify({"error": err}), 400
    if not class_name:
        return jsonify({"error": "class_name is required"}), 400
    return jsonify(ga_engine.generate_student_timetable(shift, dept, term, class_name, shared_lab=shared_lab))


@app.route("/api/faculty-list", methods=["POST"])
def api_faculty_list():
    """Faculty names to populate the Faculty Timetable dropdown, for the
    chosen shift/department/term.
    Body: { "shift", "department", "term" }
    """
    body = request.get_json(silent=True) or {}
    shift = body.get("shift") or ga_engine.SHIFT_LABELS[0]
    dept = body.get("department") or (ga_engine.DEPARTMENTS[0] if ga_engine.DEPARTMENTS else "")
    term = body.get("term") or "ODD"

    err = _validate(shift, term)
    if err:
        return jsonify({"error": err}), 400

    return jsonify({"faculty": ga_engine.list_faculty_for_shift(shift, dept, term)})


@app.route("/api/faculty", methods=["POST"])
def api_faculty():
    """Runs the GA and returns ONE faculty's combined timetable across
    every class in the shift.
    Body: { "shift", "department", "term", "faculty" }
    """
    body = request.get_json(silent=True) or {}
    shift = body.get("shift") or ga_engine.SHIFT_LABELS[0]
    dept = body.get("department") or (ga_engine.DEPARTMENTS[0] if ga_engine.DEPARTMENTS else "")
    term = body.get("term") or "ODD"
    faculty = body.get("faculty")
    shared_lab = bool(body.get("shared_lab", False))

    err = _validate(shift, term)
    if err:
        return jsonify({"error": err}), 400
    if not faculty:
        return jsonify({"error": "faculty is required"}), 400

    result = ga_engine.generate_faculty_timetable(shift, dept, term, faculty, shared_lab=shared_lab)
    return jsonify(result)


@app.route("/api/exam-types")
def api_exam_types():
    return jsonify({"exam_types": ga_engine.EXAM_TYPES})


@app.route("/api/exam", methods=["POST"])
def api_exam():
    """Runs the GA and returns the cross-class Exam Timetable for one
    shift + exam type + term.
    Body: { "shift", "department", "term", "exam_type" }
    """
    body = request.get_json(silent=True) or {}
    shift = body.get("shift") or ga_engine.SHIFT_LABELS[0]
    dept = body.get("department") or (ga_engine.DEPARTMENTS[0] if ga_engine.DEPARTMENTS else "")
    term = body.get("term") or "ODD"
    exam_type = body.get("exam_type") or ga_engine.EXAM_TYPES[0]
    shared_lab = bool(body.get("shared_lab", False))

    err = _validate(shift, term)
    if err:
        return jsonify({"error": err}), 400
    if exam_type not in ga_engine.EXAM_TYPES:
        return jsonify({"error": f"Unknown exam_type '{exam_type}'"}), 400

    result = ga_engine.generate_exam_timetable(shift, dept, term, exam_type, shared_lab=shared_lab)
    return jsonify(result)


@app.route("/api/day-order", methods=["POST"])
def api_day_order():
    """Looks up the Day Order / Week / Working-Days / Percentage for a
    single calendar date.
    Body: { "date": "DD-MM-YYYY" }  (or "YYYY-MM-DD" from a date picker)
    """
    body = request.get_json(silent=True) or {}
    date_str = body.get("date") or ""
    result = ga_engine.get_day_order_info(date_str)
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "Invalid date")}), 400
    return jsonify(result)


def _lan_ip():
    """Best-effort guess at this PC's WiFi/LAN IP address (the one a
    phone on the same network would use), without actually sending any
    traffic anywhere."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    import os
    HOST = "0.0.0.0"   # listen on every network interface, not just this PC
    PORT = int(os.environ.get("PORT", 5000))  # cloud hosts assign PORT dynamically
    ip = _lan_ip()

    print("=" * 60)
    print("  Automatic Timetable Generator")
    print("  On this PC:      http://127.0.0.1:%d" % PORT)
    print("  On phone/other PCs on the SAME WiFi: http://%s:%d" % (ip, PORT))
    print("  (first time on this WiFi? allow the Windows Firewall")
    print("   prompt so other devices are allowed to connect)")
    print("=" * 60)

    try:
        # waitress = a proper production-grade server, more stable than
        # Flask's built-in dev server for something meant to stay running.
        from waitress import serve
        serve(app, host=HOST, port=PORT)
    except ImportError:
        # Fallback if waitress isn't installed yet - still works fine.
        app.run(host=HOST, port=PORT, debug=False)
