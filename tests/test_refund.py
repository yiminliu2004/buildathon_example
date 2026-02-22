"""
Tests for refund processing to ensure correct refund amounts.

These tests verify the fix for the bug where process_refund was using
current product prices instead of the stored order.total (price-at-purchase).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Product, Customer, Order, OrderItem, PromoCode
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


class TestRefundPriceAtPurchase:
    """Tests verifying refund uses price-at-purchase, not current prices."""

    def test_refund_uses_price_at_purchase_not_current_price(self, db_session, sample_data):
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

    def test_refund_when_price_decreases(self, db_session, sample_data):
        """
        Test that refund is correct when product price DECREASES after purchase.
        Customer should still get what they paid, not the lower current price.
        """
        product = sample_data["product"]
        customer = sample_data["customer"]
        
        # Place an order at $100.00
        order = place_order(
            db=db_session,
            customer_id=customer.id,
            items=[{"product_id": product.id, "quantity": 1}],
            promo_code_str=None
        )
        
        assert order.total == 100.00
        
        # Price decreases to $50.00
        product.price = 50.00
        db_session.commit()
        
        # Process the refund
        result = process_refund(db=db_session, order_id=order.id)
        
        # Refund should be $100.00, not $50.00
        assert result["refund_amount"] == 100.00
        assert result["refund_amount"] != 50.00


class TestRefundWithDiscounts:
    """Tests verifying refund correctly handles discounts."""

    def test_refund_uses_order_total_with_loyalty_discount(self, db_session, sample_data):
        """
        Test that refund correctly uses order.total which includes
        loyalty tier discounts that were applied at purchase time.
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

    def test_refund_with_promo_code_uses_discounted_total(self, db_session, sample_data):
        """
        Test that refund correctly uses order.total when a promo code
        was applied at purchase time.
        
        This specifically tests the scenario from the bug report where
        WELCOME20 promo code gave 20% off, but refund ignored the discount.
        """
        product = sample_data["product"]
        customer = sample_data["customer"]
        
        # Create a promo code similar to WELCOME20
        promo = PromoCode(
            code="WELCOME20",
            discount_percent=20.0,
            is_active=True,
            min_order_amount=0.0
        )
        db_session.add(promo)
        db_session.commit()
        
        # Place an order with the promo code
        # Bronze tier (0% loyalty discount) + 20% promo = 20% off
        order = place_order(
            db=db_session,
            customer_id=customer.id,
            items=[{"product_id": product.id, "quantity": 1}],
            promo_code_str="WELCOME20"
        )
        
        # Order should have 20% discount applied
        # subtotal = 100, promo discount = 20%, total = 80
        assert order.subtotal == 100.00
        assert order.discount_amount == 20.00
        assert order.total == 80.00
        
        # Process refund
        result = process_refund(db=db_session, order_id=order.id)
        
        # Refund should be $80 (discounted price), not $100 (full price)
        assert result["refund_amount"] == 80.00
        assert result["refund_amount"] != 100.00

    def test_refund_with_price_change_and_discount(self, db_session, sample_data):
        """
        Test the combined scenario: price changed AND discount was applied.
        Refund should still use the original order.total.
        """
        product = sample_data["product"]
        customer = sample_data["customer"]
        
        # Make customer gold tier for 10% discount
        customer.loyalty_tier = "gold"
        db_session.commit()
        
        # Place an order at $100, gold gets 10% off = $90 total
        order = place_order(
            db=db_session,
            customer_id=customer.id,
            items=[{"product_id": product.id, "quantity": 1}],
            promo_code_str=None
        )
        
        assert order.total == 90.00
        
        # Price increases to $200 after purchase
        product.price = 200.00
        db_session.commit()
        
        # Process refund
        result = process_refund(db=db_session, order_id=order.id)
        
        # Refund should be $90 (original discounted total)
        # NOT $200 (current price) or $180 (current price with 10% off)
        assert result["refund_amount"] == 90.00


class TestRefundMultipleItems:
    """Tests for refunds with multiple order items."""

    def test_refund_multiple_items_uses_order_total(self, db_session, sample_data):
        """
        Test refund with multiple items uses order.total, not sum of current prices.
        """
        product1 = sample_data["product"]
        customer = sample_data["customer"]
        
        # Add a second product
        product2 = Product(
            name="Second Product",
            description="Another product",
            price=50.00,
            stock=10
        )
        db_session.add(product2)
        db_session.commit()
        db_session.refresh(product2)
        
        # Place order: 1x$100 + 2x$50 = $200 total
        order = place_order(
            db=db_session,
            customer_id=customer.id,
            items=[
                {"product_id": product1.id, "quantity": 1},
                {"product_id": product2.id, "quantity": 2},
            ],
            promo_code_str=None
        )
        
        assert order.total == 200.00
        
        # Change prices after purchase
        product1.price = 150.00  # was 100
        product2.price = 75.00   # was 50
        db_session.commit()
        
        # Process refund
        result = process_refund(db=db_session, order_id=order.id)
        
        # Refund should be $200 (original total)
        # NOT $300 (1x150 + 2x75 = current prices)
        assert result["refund_amount"] == 200.00
        assert result["refund_amount"] != 300.00


class TestRefundStockRestoration:
    """Tests for stock restoration during refunds."""

    def test_refund_restores_stock(self, db_session, sample_data):
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

    def test_refund_restores_stock_multiple_items(self, db_session, sample_data):
        """
        Test that refund restores stock for all items in the order.
        """
        product1 = sample_data["product"]
        customer = sample_data["customer"]
        
        product2 = Product(
            name="Second Product",
            description="Another product",
            price=50.00,
            stock=20
        )
        db_session.add(product2)
        db_session.commit()
        db_session.refresh(product2)
        
        initial_stock1 = product1.stock  # 10
        initial_stock2 = product2.stock  # 20
        
        # Place order
        order = place_order(
            db=db_session,
            customer_id=customer.id,
            items=[
                {"product_id": product1.id, "quantity": 2},
                {"product_id": product2.id, "quantity": 5},
            ],
            promo_code_str=None
        )
        
        db_session.refresh(product1)
        db_session.refresh(product2)
        assert product1.stock == 8
        assert product2.stock == 15
        
        # Process refund
        process_refund(db=db_session, order_id=order.id)
        
        db_session.refresh(product1)
        db_session.refresh(product2)
        assert product1.stock == initial_stock1
        assert product2.stock == initial_stock2


class TestRefundErrorHandling:
    """Tests for refund error handling."""

    def test_refund_already_refunded_order_raises_error(self, db_session, sample_data):
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

    def test_refund_nonexistent_order_raises_error(self, db_session):
        """
        Test that attempting to refund a non-existent order raises an error.
        """
        with pytest.raises(ValueError, match="Order not found"):
            process_refund(db=db_session, order_id=99999)


class TestRefundLoyaltyPoints:
    """Tests for loyalty points adjustments during refunds."""

    def test_refund_deducts_correct_loyalty_points(self, db_session, sample_data):
        """
        Test that refund deducts the correct number of loyalty points
        based on the refund amount (not current prices).
        """
        product = sample_data["product"]
        customer = sample_data["customer"]
        
        initial_points = customer.loyalty_points
        
        # Place order for $100
        order = place_order(
            db=db_session,
            customer_id=customer.id,
            items=[{"product_id": product.id, "quantity": 1}],
            promo_code_str=None
        )
        
        db_session.refresh(customer)
        points_after_order = customer.loyalty_points
        assert points_after_order == initial_points + 100  # 1 point per dollar
        
        # Change price after purchase
        product.price = 200.00
        db_session.commit()
        
        # Process refund
        process_refund(db=db_session, order_id=order.id)
        
        db_session.refresh(customer)
        # Should deduct 100 points (based on $100 refund), not 200
        assert customer.loyalty_points == initial_points
