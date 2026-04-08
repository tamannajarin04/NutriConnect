"""
app/routes/payment.py
Order payments + wallet top-up checkout flow.
Wallet top-up is now staged first and only credited after payment confirmation.
"""
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, session
)
from flask_login import login_required, current_user

from app.models import (
    db, Order, OrderTimeline, Wallet,
    WalletTransaction, PaymentTransaction, Notification
)

payment_bp = Blueprint("payment", __name__)

CASHBACK_PERCENT = 2.0
TOPUP_SESSION_KEY = "pending_wallet_topup_amount"

TOPUP_METHOD_LABELS = {
    "bkash": "bKash",
    "nagad": "Nagad",
    "rocket": "Rocket",
    "card": "Card",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def get_or_create_wallet(user_id):
    wallet = Wallet.query.filter_by(user_id=user_id).first()
    if not wallet:
        wallet = Wallet(user_id=user_id, balance=0.0)
        db.session.add(wallet)
        db.session.flush()
    return wallet


def is_order_paid(order):
    return PaymentTransaction.query.filter_by(
        order_id=order.id,
        status="success"
    ).first() is not None


def validate_topup_amount(raw_amount):
    try:
        amount = round(float(raw_amount or 0), 2)
    except (TypeError, ValueError):
        return None

    if amount < 1:
        return None
    if amount > 10000:
        return None
    return amount


def get_pending_topup_amount():
    amount = session.get(TOPUP_SESSION_KEY)
    if amount is None:
        return None
    try:
        return round(float(amount), 2)
    except (TypeError, ValueError):
        session.pop(TOPUP_SESSION_KEY, None)
        return None


def clear_pending_topup():
    session.pop(TOPUP_SESSION_KEY, None)


def mark_order_paid(order, method):
    """
    Record payment safely.

    Rules:
    - If the order is still pending, payment auto-confirms it.
    - If provider already moved it beyond pending, do NOT move it backward.
    - If order is cancelled, payment must not be applied.
    """
    current_status = (order.status or "pending").strip().lower()
    method_label = TOPUP_METHOD_LABELS.get(method, method.replace("_", " ").title())

    if current_status == "cancelled":
        return False

    if current_status == "pending":
        order.status = "confirmed"
        db.session.add(OrderTimeline(
            order_id=order.id,
            status="confirmed",
            note=f"Payment received via {method_label}"
        ))
    else:
        db.session.add(OrderTimeline(
            order_id=order.id,
            status=current_status,
            note=f"Payment received via {method_label}"
        ))

    cashback = round(order.total_price * CASHBACK_PERCENT / 100, 2)
    if cashback > 0:
        wallet = get_or_create_wallet(order.user_id)
        wallet.credit(
            cashback,
            f"{CASHBACK_PERCENT}% cashback for order {order.order_number}",
            ref=order.order_number
        )

    items_preview = ", ".join(
        [f"{i.food_name} ×{i.quantity}" for i in order.items.limit(3).all()]
    )
    more_items = order.items.count() - 3
    if more_items > 0:
        items_preview += f" +{more_items} more"

    buyer_name = order.customer.full_name or order.customer.username

    db.session.add(Notification(
        user_id=order.user_id,
        type="payment_sent",
        title="✅ Payment Sent Successfully!",
        message=(
            f"Your payment of ${order.total_price:.2f} for order "
            f"{order.order_number} was sent via {method_label}. "
            f"Items: {items_preview}. "
            f"You earned ${cashback:.2f} cashback!"
        ),
        link=f"/dashboard/order/{order.id}/receipt",
        is_read=False
    ))

    db.session.add(Notification(
        user_id=order.provider_id,
        type="payment_received",
        title="💰 Payment Received!",
        message=(
            f"{buyer_name} paid ${order.total_price:.2f} for order "
            f"{order.order_number} via {method_label}. "
            f"Items: {items_preview}. "
            f"Please continue the order flow."
        ),
        link=f"/provider/orders/{order.id}",
        is_read=False
    ))

    return True


# ══════════════════════════════════════════════════════
#  ORDER PAYMENT PAGE
# ══════════════════════════════════════════════════════

@payment_bp.route("/pay/<int:order_id>")
@login_required
def pay(order_id):
    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first_or_404()

    if (order.status or "").strip().lower() == "cancelled":
        flash("This order was cancelled and can no longer be paid.", "warning")
        return redirect(url_for("orders.order_detail", order_id=order.id))

    if is_order_paid(order):
        flash("This order is already paid.", "info")
        return redirect(url_for("payment.receipt", order_id=order.id))

    wallet = get_or_create_wallet(current_user.id)
    db.session.commit()

    return render_template("payment/pay.html", order=order, wallet=wallet)


@payment_bp.route("/pay/<int:order_id>/confirm", methods=["POST"])
@login_required
def confirm_payment_ajax(order_id):
    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first_or_404()

    if (order.status or "").strip().lower() == "cancelled":
        return jsonify({
            "success": False,
            "message": "This order was cancelled and can no longer be paid."
        }), 400

    if is_order_paid(order):
        return jsonify({"success": True, "already_paid": True})

    data = request.get_json(silent=True) or {}
    method = (data.get("method") or "unknown").strip().lower()
    phone = (data.get("phone") or "").strip()

    txn = PaymentTransaction(
        order_id=order.id,
        user_id=current_user.id,
        method=method,
        amount=order.total_price,
        currency="USD",
        status="success",
        phone_number=phone,
        wallet_amount=order.total_price if method == "wallet" else 0.0,
        gateway_amount=0.0 if method == "wallet" else order.total_price,
    )
    db.session.add(txn)

    if not mark_order_paid(order, method):
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "This order was cancelled and can no longer be paid."
        }), 400

    db.session.commit()
    return jsonify({"success": True})


@payment_bp.route("/pay/<int:order_id>/wallet", methods=["POST"])
@login_required
def pay_with_wallet(order_id):
    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first_or_404()

    if (order.status or "").strip().lower() == "cancelled":
        flash("This order was cancelled and can no longer be paid.", "warning")
        return redirect(url_for("orders.order_detail", order_id=order.id))

    if is_order_paid(order):
        flash("This order is already paid.", "info")
        return redirect(url_for("payment.receipt", order_id=order.id))

    wallet = get_or_create_wallet(current_user.id)

    if wallet.balance < order.total_price:
        flash(
            f"Insufficient wallet balance. Need ${order.total_price:.2f}, have ${wallet.balance:.2f}.",
            "danger"
        )
        return redirect(url_for("payment.pay", order_id=order.id))

    result = wallet.debit(
        order.total_price,
        f"Payment for order {order.order_number}",
        ref=order.order_number
    )
    if not result:
        flash("Wallet payment failed.", "danger")
        return redirect(url_for("payment.pay", order_id=order.id))

    txn = PaymentTransaction(
        order_id=order.id,
        user_id=current_user.id,
        method="wallet",
        amount=order.total_price,
        currency="USD",
        status="success",
        wallet_amount=order.total_price,
        gateway_amount=0.0,
    )
    db.session.add(txn)

    if not mark_order_paid(order, "wallet"):
        db.session.rollback()
        flash("This order was cancelled and can no longer be paid.", "warning")
        return redirect(url_for("orders.order_detail", order_id=order.id))

    db.session.commit()

    flash(f"Payment successful! ${order.total_price:.2f} deducted from wallet.", "success")
    return redirect(url_for("payment.receipt", order_id=order.id))


@payment_bp.route("/pay/<int:order_id>/done")
@login_required
def confirm_payment(order_id):
    return redirect(url_for("payment.receipt", order_id=order_id))


@payment_bp.route("/order/<int:order_id>/receipt")
@login_required
def receipt(order_id):
    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first_or_404()
    items = order.items.all()
    txn = PaymentTransaction.query.filter_by(
        order_id=order.id,
        status="success"
    ).first()
    return render_template("payment/receipt.html", order=order, items=items, txn=txn)


# ══════════════════════════════════════════════════════
#  WALLET DASHBOARD
# ══════════════════════════════════════════════════════

@payment_bp.route("/wallet")
@login_required
def wallet_dashboard():
    wallet = get_or_create_wallet(current_user.id)
    db.session.commit()

    page = request.args.get("page", 1, type=int)
    transactions = wallet.transactions.order_by(
        WalletTransaction.created_at.desc()
    ).paginate(page=page, per_page=15, error_out=False)

    return render_template(
        "payment/wallet.html",
        wallet=wallet,
        transactions=transactions
    )


@payment_bp.route("/wallet/topup", methods=["POST"])
@login_required
def wallet_topup():
    amount = validate_topup_amount(request.form.get("amount"))

    if amount is None:
        flash("Please enter a valid top-up amount between $1 and $10,000.", "warning")
        return redirect(url_for("payment.wallet_dashboard"))

    session[TOPUP_SESSION_KEY] = amount
    return redirect(url_for("payment.wallet_topup_pay"))


@payment_bp.route("/wallet/topup/pay")
@login_required
def wallet_topup_pay():
    amount = get_pending_topup_amount()
    if amount is None:
        flash("Please enter a wallet top-up amount first.", "warning")
        return redirect(url_for("payment.wallet_dashboard"))

    wallet = get_or_create_wallet(current_user.id)
    db.session.commit()

    return render_template(
        "payment/topup_pay.html",
        amount=amount,
        wallet=wallet
    )


@payment_bp.route("/wallet/topup/confirm", methods=["POST"])
@login_required
def wallet_topup_confirm_ajax():
    amount = get_pending_topup_amount()
    if amount is None:
        return jsonify({
            "success": False,
            "error": "No pending wallet top-up found. Please start again."
        }), 400

    data = request.get_json(silent=True) or {}
    method = (data.get("method") or "").strip().lower()

    if method not in TOPUP_METHOD_LABELS:
        return jsonify({
            "success": False,
            "error": "Invalid payment method selected."
        }), 400

    wallet = get_or_create_wallet(current_user.id)
    wallet.credit(
        amount,
        f"Wallet top-up via {TOPUP_METHOD_LABELS[method]}",
        ref=f"TOPUP-{method.upper()}"
    )

    db.session.add(Notification(
        user_id=current_user.id,
        type="wallet_topup",
        title="💳 Wallet Topped Up Successfully!",
        message=(
            f"${amount:.2f} was added to your NutriConnect wallet via "
            f"{TOPUP_METHOD_LABELS[method]}. New balance: ${wallet.balance:.2f}."
        ),
        link=url_for("payment.wallet_dashboard"),
        is_read=False
    ))

    db.session.commit()
    clear_pending_topup()

    return jsonify({
        "success": True,
        "amount": f"{amount:.2f}",
        "new_balance": f"{wallet.balance:.2f}",
        "method_label": TOPUP_METHOD_LABELS[method],
        "redirect_url": url_for("payment.wallet_dashboard")
    })


@payment_bp.route("/wallet/topup/cancel")
@login_required
def wallet_topup_cancel():
    clear_pending_topup()
    flash("Wallet top-up cancelled.", "info")
    return redirect(url_for("payment.wallet_dashboard"))


# ══════════════════════════════════════════════════════
#  NOTIFICATIONS
# ══════════════════════════════════════════════════════

@payment_bp.route("/notifications")
@login_required
def notifications():
    notifs = current_user.notifications.order_by(
        Notification.created_at.desc()
    ).limit(50).all()

    current_user.notifications.filter_by(is_read=False).update({"is_read": True})
    db.session.commit()

    return render_template("payment/notifications.html", notifications=notifs)


@payment_bp.route("/notifications/mark-read", methods=["POST"])
@login_required
def mark_notifications_read():
    current_user.notifications.filter_by(is_read=False).update({"is_read": True})
    db.session.commit()
    return jsonify({"success": True})


@payment_bp.route("/notifications/count")
@login_required
def notification_count():
    count = current_user.notifications.filter_by(is_read=False).count()
    return jsonify({"count": count})