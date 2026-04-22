from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Where is this comment attached
    module = Column(String, nullable=False, index=True)       # receivables | payables | sales | purchases
    entity_type = Column(String, nullable=False, index=True)  # snapshot | buyer | supplier | product | ...
    entity_id = Column(String, nullable=False, index=True)    # str-ified ID of the entity

    text = Column(Text, nullable=False)

    # Relationship back to user (for username display)
    user = relationship("User", foreign_keys=[user_id])
