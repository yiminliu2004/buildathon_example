"""
Tests for refund processing to ensure correct refund amounts.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Product, Customer, Order, OrderItem, PromoCode
from services import place_order, process_refund


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
    """Set up sample products, customers, and promo codes."""
    product = Product(name="Test Product", description="A test product", price=100.00, stock=10)
    customer = Customer(name="Test Customer", email="test@example.com", loyalty_points=0, loyalty_tier="bronze")
    promo = PromoCode(code="SAVE20", discount_percent=20.0, is_active=True, min_order_amount=0.0)
    
    db_session.add_all([product, customer, promo])
    db_session.commit()
    db_session.refresh(product)
    db_session.refresh(customer)
    db_session.refresh(promo)
    
    return {"product": product, "customer": customer, "promo": promo}


def test_refund_uses_order_total(db_session, sample_data):
    """Test that refund amount equals the order.total (what customer paid)."""
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    # Place an order
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 1}],
    )
    
    # Verify order total
    assert order.total == 100.00
    
    # Process refund
    result = process_refund(db=db_session, order_id=order.id)
    
    # Refund should equal order.total
    assert result["refund_amount"] == 100.00
    assert result["status"] == "refunded"


def test_refund_ignores_price_changes_after_purchase(db_session, sample_data):
    """Test that refund uses price at purchase, not current price."""
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    original_price = product.price  # 100.00
    
    # Place an order at original price
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 2}],
    )
    
    # Order total should be 2 * 100 = 200
    assert order.total == 200.00
    
    # Simulate price change after purchase
    product.price = 150.00
    db_session.commit()
    
    # Process refund
    result = process_refund(db=db_session, order_id=order.id)
    
    # Refund should still be 200.00 (what customer paid), NOT 300.00 (new price * quantity)
    assert result["refund_amount"] == 200.00


def test_refund_includes_discounts_applied(db_session, sample_data):
    """Test that refund amount reflects discounts applied at checkout."""
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    # Place an order with 20% promo code
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 1}],
        promo_code_str="SAVE20",
    )
    
    # Order total should be 100 - 20% = 80.00
    assert order.total == 80.00
    assert order.discount_amount == 20.00
    
    # Process refund
    result = process_refund(db=db_session, order_id=order.id)
    
    # Refund should be 80.00 (discounted amount), NOT 100.00 (full price)
    assert result["refund_amount"] == 80.00


def test_refund_restores_stock(db_session, sample_data):
    """Test that refund restores product stock."""
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    initial_stock = product.stock  # 10
    
    # Place an order for 3 items
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 3}],
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
    """Test that refunding an already refunded order raises an error."""
    product = sample_data["product"]
    customer = sample_data["customer"]
    
    order = place_order(
        db=db_session,
        customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 1}],
    )
    
    # First refund should succeed
    process_refund(db=db_session, order_id=order.id)
    
    # Second refund should fail
    with pytest.raises(ValueError, match="Order already refunded"):
        process_refund(db=db_session, order_id=order.id)


def test_refund_nonexistent_order_raises_error(db_session):
    """Test that refunding a non-existent order raises an error."""
    with pytest.raises(ValueError, match="Order not found"):
        process_refund(db=db_session, order_id=99999)
