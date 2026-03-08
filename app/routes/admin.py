from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import current_user
from app.models import db, User, Role, RoleUpgradeRequest
from ._rbac import role_required

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/dashboard")
@role_required("admin")
def dashboard():
    # Admin dashboard will be clean, but we still keep counts if you want to show them later
    pending_count = RoleUpgradeRequest.query.filter_by(status="pending").count()
    total_count = RoleUpgradeRequest.query.count()
    approved_count = RoleUpgradeRequest.query.filter_by(status="approved").count()
    rejected_count = RoleUpgradeRequest.query.filter_by(status="rejected").count()

    return render_template(
        "dashboard/admin_dashboard.html",
        user=current_user,
        pending_count=pending_count,
        total_count=total_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
    )


@admin_bp.route("/role-requests")
@role_required("admin")
def role_requests():
    pending_requests = (
        RoleUpgradeRequest.query.filter_by(status="pending")
        .order_by(RoleUpgradeRequest.created_at.desc())
        .all()
    )

    all_requests = (
        RoleUpgradeRequest.query.order_by(RoleUpgradeRequest.created_at.desc())
        .limit(100)
        .all()
    )

    # ✅ counts moved here (so admin dashboard stays clean)
    pending_count = RoleUpgradeRequest.query.filter_by(status="pending").count()
    total_count = RoleUpgradeRequest.query.count()
    approved_count = RoleUpgradeRequest.query.filter_by(status="approved").count()
    rejected_count = RoleUpgradeRequest.query.filter_by(status="rejected").count()

    return render_template(
        "dashboard/admin_role_requests.html",
        user=current_user,
        pending_requests=pending_requests,
        all_requests=all_requests,
        pending_count=pending_count,
        total_count=total_count,
        approved_count=approved_count,
        rejected_count=rejected_count
    )


@admin_bp.route("/upgrade-requests/<int:req_id>/approve", methods=["POST"])
@role_required("admin")
def approve_request(req_id):
    req = RoleUpgradeRequest.query.get_or_404(req_id)
    if req.status != "pending":
        flash("Request already processed.", "warning")
        return redirect(url_for("admin.role_requests"))

    user = User.query.get(req.user_id)
    target_role = Role.query.filter_by(name=req.requested_role).first()

    if not user or not target_role:
        flash("Invalid request/user/role.", "danger")
        return redirect(url_for("admin.role_requests"))

    # ✅ single active role
    user.roles = [target_role]

    req.status = "approved"
    req.admin_comment = (request.form.get("admin_comment") or "").strip()

    db.session.commit()
    flash(f"Approved: {user.username} is now {target_role.name}.", "success")
    return redirect(url_for("admin.role_requests"))


@admin_bp.route("/upgrade-requests/<int:req_id>/reject", methods=["POST"])
@role_required("admin")
def reject_request(req_id):
    req = RoleUpgradeRequest.query.get_or_404(req_id)
    if req.status != "pending":
        flash("Request already processed.", "warning")
        return redirect(url_for("admin.role_requests"))

    req.status = "rejected"
    req.admin_comment = (request.form.get("admin_comment") or "").strip()

    db.session.commit()
    flash("Request rejected.", "info")
    return redirect(url_for("admin.role_requests"))