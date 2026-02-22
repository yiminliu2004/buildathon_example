"""
Tests for refund processing - verifies refund uses order.total (price at purchase)
rather than current product prices.
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
def sample_product(db_session):
    """Create a sample product."""
    product = Product(
        name="Test Widget",
        description="A test product",
        price=100.00,
        stock=10
    )
    db_session.add(product)
    db_session.commit()
    db_session.refresh(product)
    return product


@pytest.fixture
def sample_customer(db_session):
    """Create a sample customer."""
    customer = Customer(
        name="Test User",
        email="test@example.com",
        loyalty_points=0,
        loyalty_tier="bronze"
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def test_refund_uses_order_total_not_current_price(db_session, sample_product, sample_customer):
    """
    Test that refund amount equals order.total (price at purchase),
    not the current product price.
    """
    # Place an order at original price ($100)
    order = place_order(
        db=db_session,
        customer_id=sample_customer.id,
        items=[{"product_id": sample_product.id, "quantity": 2}]
    )
    
    original_total = order.total
    assert original_total == 200.00  # 2 x $100
    
    # Change the product price AFTER the order was placed
    sample_product.price = 150.00  # Price increased by 50%
    db_session.commit()
    
    # Process refund
    result = process_refund(db=db_session, order_id=order.id)
    
    # Refund should be the original amount paid ($200), NOT the new price ($300)
    assert result["refund_amount"] == 200.00
    assert result["refund_amount"] == original_total
    assert result["refund_amount"] != 300.00  # Would be wrong if using current price


def test_refund_restores_stock(db_session, sample_product, sample_customer):
    """
    Test that refund correctly restores product stock.
    """
    initial_stock = sample_product.stock  # 10
    
    # Place an order (reduces stock by 3)
    order = place_order(
        db=db_session,
        customer_id=sample_customer.id,
        items=[{"product_id": sample_product.id, "quantity": 3}]
    )
    
    db_session.refresh(sample_product)
    assert sample_product.stock == initial_stock - 3  # 7
    
    # Process refund (should restore stock)
    process_refund(db=db_session, order_id=order.id)
    
    db_session.refresh(sample_product)
    assert sample_product.stock == initial_stock  # Back to 10


def test_refund_already_refunded_order_raises_error(db_session, sample_product, sample_customer):
    """
    Test that attempting to refund an already-refunded order raises ValueError.
    """
    order = place_order(
        db=db_session,
        customer_id=sample_customer.id,
        items=[{"product_id": sample_product.id, "quantity": 1}]
    )
    
    # First refund should succeed
    process_refund(db=db_session, order_id=order.id)
    
    # Second refund should raise error
    with pytest.raises(ValueError, match="Order already refunded"):
        process_refund(db=db_session, order_id=order.id)


def test_refund_nonexistent_order_raises_error(db_session):
    """
    Test that attempting to refund a non-existent order raises ValueError.
    """
    with pytest.raises(ValueError, match="Order not found"):
        process_refund(db=db_session, order_id=99999)


def test_refund_deducts_loyalty_points_correctly(db_session, sample_product, sample_customer):
    """
    Test that refund deducts loyalty points based on original order total,
    not recalculated current prices.
    """
    initial_points = sample_customer.loyalty_points  # 0
    
    # Place order - customer earns points based on $100 total
    order = place_order(
        db=db_session,
        customer_id=sample_customer.id,
        items=[{"product_id": sample_product.id, "quantity": 1}]
    )
    
    db_session.refresh(sample_customer)
    points_after_order = sample_customer.loyalty_points
    assert points_after_order == initial_points + 100  # Earned 100 points for $100 order
    
    # Change price after order
    sample_product.price = 200.00
    db_session.commit()
    
    # Process refund - should deduct based on original $100, not new $200
    process_refund(db=db_session, order_id=order.id)
    
    db_session.refresh(sample_customer)
    # Points should be deducted based on order.total ($100), not current price ($200)
    assert sample_customer.loyalty_points == initial_points
