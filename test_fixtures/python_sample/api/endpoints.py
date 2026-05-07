"""FastAPI service that reads from DB and exposes via REST."""

from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from .models import User, Order
from .database import get_db

app = FastAPI()


@app.get("/api/users/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    """Fetch a user by ID from the database."""
    user = db.query(User).filter(User.id == user_id).first()
    return {"id": user.id, "name": user.name, "email": user.email}


@app.get("/api/users/{user_id}/orders")
def get_user_orders(user_id: int, db: Session = Depends(get_db)):
    """Get all orders for a user, joined with user info."""
    orders = (
        db.query(Order, User)
        .join(User, Order.user_id == User.id)
        .filter(User.id == user_id)
        .all()
    )
    return [
        {
            "order_id": order.id,
            "product": order.product_name,
            "amount": float(order.amount),
            "user_name": user.name,
        }
        for order, user in orders
    ]


@app.post("/api/orders")
def create_order(order_data: dict, db: Session = Depends(get_db)):
    """Create a new order in the database."""
    order = Order(
        user_id=order_data["user_id"],
        product_name=order_data["product"],
        amount=order_data["amount"],
    )
    db.add(order)
    db.commit()
    return {"order_id": order.id, "status": "created"}
