"""
app/routes/payment.py
Both user (payment sent) and provider (payment received) get notifications.
"""
import os
from datetime import datetime
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, jsonify)
from flask_login import login_required, current_user

from app.models import (db, Order, OrderTimeline, Wallet,
                        WalletTransaction, PaymentTransaction, Notification)

payment_bp = Blueprint("payment", __name__)

CASHBACK_PERCENT = 2.0


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
        order_id=order.id, status="success"
    ).first() is not None


def mark_order_paid(order, method):
    """Confirm order, give cashback, notify BOTH user and provider."""
    order.status = "confirmed"

    db.session.add(OrderTimeline(
        order_id=order.id,
        status="confirmed",
        note=f"Payment received via {method}"
    ))

    # ── 2% cashback to buyer ──────────────────────
    cashback = round(order.total_price * CASHBACK_PERCENT / 100, 2)
    if cashback > 0:
        wallet = get_or_create_wallet(order.user_id)
        wallet.credit(
            cashback,
            f"{CASHBACK_PERCENT}% cashback for order {order.order_number}",
            ref=order.order_number
        )

    method_label = method.replace("_", " ").title()
    items_preview = ", ".join(
        [f"{i.food_name} ×{i.quantity}" for i in order.items.limit(3).all()]
    )
    more_items = order.items.count() - 3
    if more_items > 0:
        items_preview += f" +{more_items} more"

    buyer_name    = order.customer.full_name or order.customer.username
    provider_name = order.provider_user.full_name or order.provider_user.username

    # ── Notify BUYER — payment sent ──────────────
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

    # ── Notify PROVIDER — payment received ───────
    db.session.add(Notification(
        user_id=order.provider_id,
        type="payment_received",
        title="💰 Payment Received!",
        message=(
            f"{buyer_name} paid ${order.total_price:.2f} for order "
            f"{order.order_number} via {method_label}. "
            f"Items: {items_preview}. "
            f"Please prepare the order."
        ),
        link=f"/provider/orders/{order.id}",
        is_read=False
    ))


# ══════════════════════════════════════════════════════
#  PAYMENT PAGE
# ══════════════════════════════════════════════════════

@payment_bp.route("/pay/<int:order_id>")
@login_required
def pay(order_id):
    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first_or_404()

    if is_order_paid(order):
        flash("This order is already paid.", "info")
        return redirect(url_for("payment.receipt", order_id=order.id))

    wallet = get_or_create_wallet(current_user.id)
    db.session.commit()

    return render_template("payment/pay.html", order=order, wallet=wallet)


# ══════════════════════════════════════════════════════
#  AJAX — confirm after simulated phone+PIN payment
# ══════════════════════════════════════════════════════

@payment_bp.route("/pay/<int:order_id>/confirm", methods=["POST"])
@login_required
def confirm_payment_ajax(order_id):
    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first_or_404()

    if is_order_paid(order):
        return jsonify({"success": True, "already_paid": True})

    data   = request.get_json()
    method = data.get("method", "unknown")
    phone  = data.get("phone", "")

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
    mark_order_paid(order, method)
    db.session.commit()

    return jsonify({"success": True})


# ══════════════════════════════════════════════════════
#  WALLET FULL PAYMENT
# ══════════════════════════════════════════════════════

@payment_bp.route("/pay/<int:order_id>/wallet", methods=["POST"])
@login_required
def pay_with_wallet(order_id):
    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first_or_404()

    if is_order_paid(order):
        flash("This order is already paid.", "info")
        return redirect(url_for("payment.receipt", order_id=order.id))

    wallet = get_or_create_wallet(current_user.id)

    if wallet.balance < order.total_price:
        flash(f"Insufficient wallet balance. Need ${order.total_price:.2f}, have ${wallet.balance:.2f}.", "danger")
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
    mark_order_paid(order, "wallet")
    db.session.commit()

    flash(f"Payment successful! ${order.total_price:.2f} deducted from wallet.", "success")
    return redirect(url_for("payment.receipt", order_id=order.id))


# ══════════════════════════════════════════════════════
#  CONFIRM REDIRECT & RECEIPT
# ══════════════════════════════════════════════════════

@payment_bp.route("/pay/<int:order_id>/done")
@login_required
def confirm_payment(order_id):
    return redirect(url_for("payment.receipt", order_id=order_id))


@payment_bp.route("/order/<int:order_id>/receipt")
@login_required
def receipt(order_id):
    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first_or_404()
    items = order.items.all()
    txn   = PaymentTransaction.query.filter_by(
        order_id=order.id, status="success"
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
    return render_template("payment/wallet.html", wallet=wallet, transactions=transactions)


@payment_bp.route("/wallet/topup", methods=["POST"])
@login_required
def wallet_topup():
    try:
        amount = float(request.form.get("amount", 0))
    except ValueError:
        flash("Invalid amount.", "danger")
        return redirect(url_for("payment.wallet_dashboard"))

    if amount < 1:
        flash("Minimum top-up is $1.", "warning")
        return redirect(url_for("payment.wallet_dashboard"))

    if amount > 10000:
        flash("Maximum top-up is $10,000.", "warning")
        return redirect(url_for("payment.wallet_dashboard"))

    wallet = get_or_create_wallet(current_user.id)
    wallet.credit(amount, "Manual wallet top-up", ref="TOPUP")

    # Notify user about top-up
    db.session.add(Notification(
        user_id=current_user.id,
        type="wallet_topup",
        title="💳 Wallet Topped Up!",
        message=f"${amount:.2f} has been added to your NutriConnect wallet. New balance: ${wallet.balance:.2f}.",
        link="/dashboard/wallet",
        is_read=False
    ))

    db.session.commit()
    flash(f"${amount:.2f} added to your wallet!", "success")
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