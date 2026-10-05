from dataclasses import dataclass
from typing import Optional


@dataclass
class Product:
    name: str
    price: float
    image_url: str
    affiliate_link: str
    sold: Optional[int] = None
    rating: Optional[float] = None
    discount: Optional[str] = None
    position: Optional[int] = None
    is_tagged_affiliate_link: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "price": self.price,
            "image_url": self.image_url,
            "affiliate_link": self.affiliate_link,
            "sold": self.sold,
            "rating": self.rating,
            "discount": self.discount,
            "position": self.position,
        }
