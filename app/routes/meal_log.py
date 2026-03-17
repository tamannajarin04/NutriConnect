from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.models import db, MealLog, MEAL_GOAL_CHOICES
from datetime import datetime, date, timedelta

meal_log_bp = Blueprint("meal_log", __name__)


# ---------------------------
# Helper: "Logical Today"
# ---------------------------
# The new day starts at 05:00 AM, not at midnight.
# Before 5 AM → still treat it as yesterday's journal.
# At or after 5 AM → treat it as today's journal.
#
# DAY_START_HOUR controls that threshold.
# Change this value to 6 (or any hour) to shift the boundary.
DAY_START_HOUR = 5   # 5 = 05:00 AM

def get_logical_today() -> date:
    """
    Return the 'active' date the user should be logging for.
    If it is currently before DAY_START_HOUR, return yesterday;
    otherwise return the calendar date.
    """
    now = datetime.now()          # server local time
    if now.hour < DAY_START_HOUR:
        return now.date() - timedelta(days=1)
    return now.date()


# ---------------------------
# View meal logs for a date
# ---------------------------
@meal_log_bp.route("/", methods=["GET"])
@login_required
def index():
    # ── Determine the "active" default date ──────────────────────────
    logical_today = get_logical_today()   # ✅ replaces date.today()

    date_str = request.args.get("date")
    try:
        selected_date = (
            datetime.strptime(date_str, "%Y-%m-%d").date()
            if date_str
            else logical_today
        )
    except ValueError:
        selected_date = logical_today

    # Guard: never allow a date past the logical today
    # (calendar today is still the hard cap for the date picker max=)
    if selected_date > logical_today:
        selected_date = logical_today

    # ── Query only the selected date's logs ──────────────────────────
    logs = (
        MealLog.query
        .filter_by(user_id=current_user.id, log_date=selected_date)
        .order_by(MealLog.logged_at.asc())
        .all()
    )

    grouped = {"breakfast": [], "lunch": [], "dinner": [], "snack": []}
    for log in logs:
        grouped.setdefault(log.meal_type, []).append(log)

    return render_template(
        "meal_log/index.html",
        grouped=grouped,
        selected_date=selected_date,
        today=logical_today,           # ✅ template uses this for "Today" button & next-arrow guard
        timedelta=timedelta,
        goal_choices=MEAL_GOAL_CHOICES,
    )


# ---------------------------
# Add a meal log entry
# ---------------------------
@meal_log_bp.route("/add", methods=["POST"])
@login_required
def add():
    food_name    = request.form.get("food_name",  "").strip()
    meal_type    = request.form.get("meal_type",  "").strip()
    quantity     = request.form.get("quantity",   "").strip()
    log_date_str = request.form.get("log_date",   "").strip()
    goal         = request.form.get("goal",       "").strip()

    logical_today = get_logical_today()   # ✅ use logical today for fallback

    try:
        log_date = (
            datetime.strptime(log_date_str, "%Y-%m-%d").date()
            if log_date_str
            else logical_today
        )
    except ValueError:
        log_date = logical_today

    # Validation
    if not food_name:
        flash("Please enter the food name.", "danger")
        return redirect(url_for("meal_log.index", date=log_date_str))

    if not meal_type or meal_type not in ["breakfast", "lunch", "dinner", "snack"]:
        flash("Please select a valid meal type.", "danger")
        return redirect(url_for("meal_log.index", date=log_date_str))

    if not quantity:
        flash("Please enter the quantity.", "danger")
        return redirect(url_for("meal_log.index", date=log_date_str))

    if goal and goal not in MEAL_GOAL_CHOICES:
        flash("Please select a valid goal.", "danger")
        return redirect(url_for("meal_log.index", date=log_date_str))

    log = MealLog(
        user_id   = current_user.id,
        food_name = food_name,
        meal_type = meal_type,
        quantity  = quantity,
        log_date  = log_date,
        logged_at = datetime.utcnow(),
        goal      = goal if goal else None,
    )

    db.session.add(log)
    db.session.commit()

    flash("Meal logged successfully!", "success")
    return redirect(url_for("meal_log.index", date=log_date.strftime("%Y-%m-%d")))


# ---------------------------
# Delete a meal log entry
# ---------------------------
@meal_log_bp.route("/delete/<int:log_id>", methods=["POST"])
@login_required
def delete(log_id):
    log = MealLog.query.filter_by(id=log_id, user_id=current_user.id).first_or_404()
    log_date = log.log_date
    db.session.delete(log)
    db.session.commit()
    flash("Meal entry removed.", "info")
    return redirect(url_for("meal_log.index", date=log_date.strftime("%Y-%m-%d")))