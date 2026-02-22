"""
Tests for refund processing to ensure correct refund amounts.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Product, Customer, Order, OrderItem
from services import process_refund, place_order


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    yield session
    session.close()


@pytest.fixture
def sample_data(db_session):
    """Set up sample products and customers."""
    product = Product(
        name="Test Product",
        description="A test product",
        price=100.00,
        stock=10
    )
    customer = Customer(
        name="Test Customer",
        email="test@example.com",
        loyalty_points=0,
        loyalty_tier="bronze"
    )
    db_session.add(product)
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(product)
    db_session.refresh(customer)
    return {"product": product, "customer": customer}


def test_refund_uses_price_at_purchase_not_current_price(db_session, sample_data):
    """
    Test that refund amount uses the price at purchase time,
    not the current product price.
    
    This tests the fix for the bug where process_refund was using
    product.price (current) instead of order.total (historical).
    """
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    # Place an order at the original price ($100.00)
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 1}],
        promo_code_str=None
    )
    
    # Verify the order total is $100.00
    assert order.total == 100.00
    original_total = order.total
    
    # Now simulate a price change - the product price increases to $150.00
    product.price = 150.00
    db_session.commit()
    
    # Process the refund
    result = process_refund(db=db_session, order_id=order.id)
    
    # The refund should be $100.00 (what the customer paid),
    # NOT $150.00 (the current price)
    assert result["refund_amount"] == original_total
    assert result["refund_amount"] == 100.00
    assert result["refund_amount"] != 150.00
    assert result["status"] == "refunded"


def test_refund_uses_order_total_with_discount(db_session, sample_data):
    """
    Test that refund correctly uses order.total which includes
    any discounts that were applied at purchase time.
    """
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    # Make customer gold tier for 10% discount
    customer.loyalty_tier = "gold"
    db_session.commit()
    
    # Place an order - gold tier gets 10% off
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 1}],
        promo_code_str=None
    )
    
    # Order should have discount applied
    # subtotal = 100, gold discount = 10%, total = 90
    assert order.subtotal == 100.00
    assert order.discount_amount == 10.00
    assert order.total == 90.00
    
    # Process refund
    result = process_refund(db=db_session, order_id=order.id)
    
    # Refund should be what customer actually paid ($90), not subtotal ($100)
    assert result["refund_amount"] == 90.00


def test_refund_restores_stock(db_session, sample_data):
    """
    Test that refund correctly restores product stock.
    """
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    initial_stock = product.stock  # 10
    
    # Place an order for 3 items
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 3}],
        promo_code_str=None
    )
    
    # Stock should be reduced
    db_session.refresh(product)
    assert product.stock == initial_stock - 3  # 7
    
    # Process refund
    process_refund(db=db_session, order_id=order.id)
    
    # Stock should be restored
    db_session.refresh(product)
    assert product.stock == initial_stock  # 10


def test_refund_already_refunded_order_raises_error(db_session, sample_data):
    """
    Test that attempting to refund an already refunded order raises an error.
    """
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 1}],
        promo_code_str=None
    )
    
    # First refund should succeed
    process_refund(db=db_session, order_id=order.id)
    
    # Second refund should raise ValueError
    with pytest.raises(ValueError, match="Order already refunded"):
        process_refund(db=db_session, order_id=order.id)


def test_refund_nonexistent_order_raises_error(db_session):
    """
    Test that attempting to refund a non-existent order raises an error.
    """
    with pytest.raises(ValueError, match="Order not found"):
        process_refund(db=db_session, order_id=99999)
