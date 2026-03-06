from functools import wraps
from flask import flash, redirect, url_for
from flask_login import login_required, current_user

def role_required(*role_names):
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            if not any(current_user.has_role(r) for r in role_names):
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("user_dashboard.index"))
            return f(*args, **kwargs)
        return wrapped
    return decorator