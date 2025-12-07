import re
import csv
import io
from dataclasses import dataclass
from typing import List, Optional

from flask import Flask, render_template, request, make_response
import PyPDF2

app = Flask(__name__)


@dataclass
class CourseRecord:
    sl_no: int
    course_code: str
    course_title: str
    course_type: str
    credits: float
    grade: str
    exam_month: str
    result_declared_on: str
    course_option: str
    course_distribution: str


# ---------- PDF PARSING ----------

def extract_text_from_pdf(file_obj) -> str:
    """Read full text from all pages of the uploaded PDF."""
    reader = PyPDF2.PdfReader(file_obj)
    pages_text = []
    for page in reader.pages:
        pages_text.append(page.extract_text())
    return "\n".join(pages_text)


def normalize_raw_text(text: str) -> str:
    # Fix missing space between Sl.No and Course Code: "6HUM1021" -> "6 HUM1021"
    text = re.sub(r"(\b\d{1,2})([A-Z]{3}\d{4})", r"\1 \2", text)

    # Fix missing space between Course Code and Title: "CSE1001Problem" -> "CSE1001 Problem"
    text = re.sub(r"([A-Z]{3}\d{4})([A-Za-z])", r"\1 \2", text)

    return text


def clean_line(line: str) -> str:
    # Remove footer text that sometimes sticks to the last row on a page
    line = re.sub(r"UNOFFICIALProvisional Grade History.*", "", line)
    return line.strip()


def parse_course_line(line: str) -> Optional[CourseRecord]:
    tokens = line.split()
    if len(tokens) < 10:
        return None

    try:
        sl_no = int(tokens[0])
    except ValueError:
        return None

    try:
        course_code = tokens[1]

        # From right side
        course_distribution = tokens[-1]
        course_option = tokens[-2]
        result_declared_on = tokens[-3]
        exam_month = tokens[-4]
        grade = tokens[-5]
        credits = float(tokens[-6])
        course_type = tokens[-7]

        # Middle part = course title
        title_tokens = tokens[2:-7]
        course_title = " ".join(title_tokens)

        return CourseRecord(
            sl_no=sl_no,
            course_code=course_code,
            course_title=course_title,
            course_type=course_type,
            credits=credits,
            grade=grade,
            exam_month=exam_month,
            result_declared_on=result_declared_on,
            course_option=course_option,
            course_distribution=course_distribution
        )
    except Exception:
        return None


def parse_grade_history(file_obj) -> List[CourseRecord]:
    raw_text = extract_text_from_pdf(file_obj)
    text = normalize_raw_text(raw_text)
    lines = text.splitlines()

    records: List[CourseRecord] = []
    current_line = ""

    for raw_line in lines:
        line = clean_line(raw_line)
        if not line:
            continue

        # Detect start of new course row: "SlNo CourseCode"
        if re.match(r"^\d+\s+[A-Z]{3}\d{4}\b", line):
            if current_line:
                rec = parse_course_line(current_line)
                if rec:
                    records.append(rec)
            current_line = line
        else:
            if current_line:
                current_line += " " + line

    if current_line:
        rec = parse_course_line(current_line)
        if rec:
            records.append(rec)

    return records


# ---------- CSV PARSING (from exported file) ----------

def parse_csv(file_obj) -> List[CourseRecord]:
    """
    Parse a CSV file that was previously exported by /download.
    Expected header:
    Sl.No, Course Code, Course Title, Course Type, Credits,
    Grade, Exam Month, Result Declared On, Course Option, Course Distribution
    """
    text_stream = io.TextIOWrapper(file_obj, encoding="utf-8")
    reader = csv.DictReader(text_stream)

    records: List[CourseRecord] = []

    for row in reader:
        # Skip completely empty rows
        if not any((row.get(col) or "").strip() for col in row.keys()):
            continue

        try:
            sl_no_str = (row.get("Sl.No") or "").strip()
            if not sl_no_str:
                continue
            sl_no = int(sl_no_str)

            course_code = (row.get("Course Code") or "").strip()
            course_title = (row.get("Course Title") or "").strip()
            course_type = (row.get("Course Type") or "").strip()

            credits_str = (row.get("Credits") or "").strip()
            credits = float(credits_str) if credits_str else 0.0

            grade = (row.get("Grade") or "").strip()
            exam_month = (row.get("Exam Month") or "").strip()
            result_declared_on = (row.get("Result Declared On") or "").strip()
            course_option = (row.get("Course Option") or "").strip()
            course_distribution = (row.get("Course Distribution") or "").strip()

            rec = CourseRecord(
                sl_no=sl_no,
                course_code=course_code,
                course_title=course_title,
                course_type=course_type,
                credits=credits,
                grade=grade,
                exam_month=exam_month,
                result_declared_on=result_declared_on,
                course_option=course_option,
                course_distribution=course_distribution,
            )
            records.append(rec)
        except Exception:
            # If any row is malformed, just skip it
            continue

    return records


# ---------- ROUTES ----------

@app.route("/", methods=["GET", "POST"])
def index():
    records: Optional[List[CourseRecord]] = None

    if request.method == "POST":
        uploaded = request.files.get("file")
        if uploaded and uploaded.filename:
            filename = uploaded.filename.lower()
            uploaded.stream.seek(0)

            if filename.endswith(".pdf"):
                records = parse_grade_history(uploaded.stream)
            elif filename.endswith(".csv"):
                records = parse_csv(uploaded.stream)
            else:
                # unsupported type -> no records (you could flash a message here)
                records = None

    return render_template("index.html", records=records)


@app.route("/download", methods=["POST"])
def download_csv():
    # Parse edited table data from form fields
    rows_data = {}
    pattern = re.compile(r"row-(\d+)-([a-z_]+)")

    for key, value in request.form.items():
        m = pattern.match(key)
        if not m:
            continue
        idx = int(m.group(1))
        field = m.group(2)
        row = rows_data.setdefault(idx, {})
        row[field] = value.strip()

    # CSV header and field order (must match parse_csv expectations)
    header = [
        "Sl.No", "Course Code", "Course Title", "Course Type", "Credits",
        "Grade", "Exam Month", "Result Declared On", "Course Option", "Course Distribution"
    ]
    field_order = [
        "sl_no", "course_code", "course_title", "course_type", "credits",
        "grade", "exam_month", "result_declared_on", "course_option", "course_distribution"
    ]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)

    for idx in sorted(rows_data.keys()):
        row = rows_data[idx]

        # Skip fully empty rows
        if not any(row.get(f, "") for f in field_order):
            continue

        writer.writerow([row.get(f, "") for f in field_order])

    csv_data = output.getvalue()
    response = make_response(csv_data)
    response.headers["Content-Type"] = "text/csv"
    response.headers["Content-Disposition"] = "attachment; filename=edited_grades.csv"
    return response


if __name__ == "__main__":
    app.run(debug=True)
