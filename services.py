"""
Business logic for the order management system.

Handles:
  - Order placement with stock validation
  - Discount calculation (loyalty tier + promo codes)
  - Refund processing
"""

from sqlalchemy.orm import Session
from models import Product, Customer, Order, OrderItem, PromoCode


# ── Loyalty tier discount mapping ─────────────────────────────────

LOYALTY_DISCOUNTS = {
    "bronze": 0.0,    # 0%
    "silver": 5.0,    # 5%
    "gold": 10.0,     # 10%
}


def calculate_discount(
    subtotal: float,
    customer: Customer,
    promo_code: PromoCode | None,
) -> tuple[float, float]:
    """Calculate the discount for an order.

    Business rule: If a customer has BOTH a loyalty discount and a promo code,
    the system should apply whichever discount is LARGER (not both).

    Args:
        subtotal: The pre-discount order total.
        customer: The customer placing the order.
        promo_code: An optional promo code applied to the order.

    Returns:
        (discount_amount, final_total)
    """
    loyalty_percent = LOYALTY_DISCOUNTS.get(customer.loyalty_tier, 0.0)

    promo_percent = 0.0
    if promo_code and promo_code.is_active and subtotal >= promo_code.min_order_amount:
        promo_percent = promo_code.discount_percent

    # Apply the loyalty discount
    loyalty_discount = subtotal * (loyalty_percent / 100.0)
    after_loyalty = subtotal - loyalty_discount

    # Apply the promo code discount on top of the loyalty discount
    promo_discount = after_loyalty * (promo_percent / 100.0)
    total_discount = loyalty_discount + promo_discount

    final_total = subtotal - total_discount
    return round(total_discount, 2), round(final_total, 2)


def place_order(
    db: Session,
    customer_id: int,
    items: list[dict],
    promo_code_str: str | None = None,
) -> Order:
    """Place a new order.

    Args:
        db: Database session.
        customer_id: ID of the customer.
        items: List of {"product_id": int, "quantity": int}.
        promo_code_str: Optional promo code string.

    Returns:
        The created Order.

    Raises:
        ValueError: If customer not found, product not found, or insufficient stock.
    """
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise ValueError("Customer not found")

    # Validate promo code
    promo_code = None
    if promo_code_str:
        promo_code = (
            db.query(PromoCode)
            .filter(PromoCode.code == promo_code_str, PromoCode.is_active == True)
            .first()
        )
        if not promo_code:
            raise ValueError(f"Invalid or expired promo code: {promo_code_str}")

    # Build order items and calculate subtotal
    order_items = []
    subtotal = 0.0

    for item in items:
        product = db.query(Product).filter(Product.id == item["product_id"]).first()
        if not product:
            raise ValueError(f"Product {item['product_id']} not found")

        quantity = item["quantity"]
        if product.stock < quantity:
            raise ValueError(
                f"Insufficient stock for '{product.name}': "
                f"requested {quantity}, available {product.stock}"
            )

        line_total = product.price * quantity
        subtotal += line_total

        order_items.append(OrderItem(
            product_id=product.id,
            quantity=quantity,
            price_at_purchase=product.price,
        ))

        # Decrement stock
        product.stock -= quantity

    # Calculate discount
    discount_amount, final_total = calculate_discount(subtotal, customer, promo_code)

    # Award loyalty points (1 point per dollar spent)
    customer.loyalty_points += int(final_total)

    # Auto-upgrade loyalty tier
    if customer.loyalty_points >= 1000:
        customer.loyalty_tier = "gold"
    elif customer.loyalty_points >= 500:
        customer.loyalty_tier = "silver"

    # Create the order
    order = Order(
        customer_id=customer.id,
        status="confirmed",
        subtotal=round(subtotal, 2),
        discount_amount=discount_amount,
        total=final_total,
        promo_code_used=promo_code_str,
    )
    db.add(order)
    db.flush()

    for oi in order_items:
        oi.order_id = order.id
        db.add(oi)

    db.commit()
    db.refresh(order)
    return order


def process_refund(db: Session, order_id: int) -> dict:
    """Process a full refund for an order.

    Business rule: The refund amount should be the TOTAL that the customer
    actually paid at the time of purchase (order.total), and stock should
    be restored based on the quantities in the order items.

    Args:
        db: Database session.
        order_id: ID of the order to refund.

    Returns:
        Dict with refund details.

    Raises:
        ValueError: If order not found or already refunded.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    if order.status == "refunded":
        raise ValueError("Order already refunded")

    # Use the actual amount the customer paid (order.total)
    # This correctly accounts for discounts and price-at-purchase
    refund_amount = order.total

    # Restore stock for each item in the order
    for item in order.items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if product:
            product.stock += item.quantity

    # Deduct loyalty points based on the original amount paid
    customer = order.customer
    customer.loyalty_points -= int(order.total)
    if customer.loyalty_points < 0:
        customer.loyalty_points = 0

    # Re-evaluate loyalty tier
    if customer.loyalty_points >= 1000:
        customer.loyalty_tier = "gold"
    elif customer.loyalty_points >= 500:
        customer.loyalty_tier = "silver"
    else:
        customer.loyalty_tier = "bronze"

    order.status = "refunded"
    order.refund_amount = round(refund_amount, 2)
    db.commit()

    return {
        "order_id": order.id,
        "refund_amount": round(refund_amount, 2),
        "status": "refunded",
    }
