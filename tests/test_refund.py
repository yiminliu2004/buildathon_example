"""
Unit tests for refund processing.

Verifies that refunds use the original purchase price, not current prices.
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
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_data(db_session):
    """Create sample product and customer for testing."""
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
    return {"product": product, "customer": customer}


def test_refund_uses_original_total(db_session, sample_data):
    """Refund amount should equal original order.total."""
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    # Place an order
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 2}],
        promo_code_str=None
    )
    
    original_total = order.total
    assert original_total == 200.00  # 2 * $100
    
    # Process refund
    result = process_refund(db_session, order.id)
    
    # Refund should match original total
    assert result["refund_amount"] == original_total
    assert result["status"] == "refunded"


def test_refund_ignores_price_changes(db_session, sample_data):
    """Refund should use price at purchase, not current price."""
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    original_price = product.price  # $100
    
    # Place an order at original price
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 1}],
        promo_code_str=None
    )
    
    original_total = order.total
    assert original_total == 100.00
    
    # Simulate price change AFTER order was placed
    product.price = 150.00  # Price increased by 50%
    db_session.commit()
    
    # Process refund
    result = process_refund(db_session, order.id)
    
    # Refund should still be $100 (original total), NOT $150 (current price)
    assert result["refund_amount"] == 100.00
    assert result["refund_amount"] != product.price


def test_refund_restores_stock(db_session, sample_data):
    """Refund should restore product stock."""
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    initial_stock = product.stock  # 10
    quantity_ordered = 3
    
    # Place an order
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": quantity_ordered}],
        promo_code_str=None
    )
    
    # Stock should be reduced after order
    db_session.refresh(product)
    assert product.stock == initial_stock - quantity_ordered  # 7
    
    # Process refund
    process_refund(db_session, order.id)
    
    # Stock should be restored
    db_session.refresh(product)
    assert product.stock == initial_stock  # 10


def test_refund_already_refunded_order_raises(db_session, sample_data):
    """Attempting to refund an already-refunded order should raise ValueError."""
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    # Place and refund an order
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 1}],
        promo_code_str=None
    )
    process_refund(db_session, order.id)
    
    # Attempting to refund again should raise
    with pytest.raises(ValueError, match="already refunded"):
        process_refund(db_session, order.id)


def test_refund_nonexistent_order_raises(db_session):
    """Attempting to refund a non-existent order should raise ValueError."""
    with pytest.raises(ValueError, match="Order not found"):
        process_refund(db_session, order_id=99999)
