import dataclasses
from enum import Enum
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


class AssetType(Enum):
    CRYPTO = "Crypto"
    STOCKS = "Stocks"


class TaxRelatedEventType(Enum):
    BUY = "Buy"
    SELL = "Sell"
    VEST = "Vest"
    STOCK_SPLIT = "Stock Split"
    # Following three do not affect the cost basis of any assets
    INCOME = "Income"
    DIVIDEND = "Dividend"
    CASH_INCOME = "Cash Income"
    # Adjusts the cost basis of the asset
    ERI = "ERI"
    # Adjusts the cost basis of the asset
    CAPITAL_RETURN = "Capital Return"


class TaxRule(Enum):
    SECTION_104 = "S104"
    SAME_DAY = "SD"
    BED_AND_BREAKFAST = "B&B"


class TaxableGainGroup(Enum):
    UNLISTED_SHARES = "Unlisted shares and securities"
    LISTED_SHARES = "Listed shares and securities"
    OTHER_PROPERTY = "Other property, assets and gains"
    DIVIDENDS = "Dividends"
    OTHER_INCOME = "Other income"
    NOTIONAL_DIVIDENDS = "Notional dividends / ERI"
    # for cgtcalc compatibility, not sure about correct usage
    CAPITAL_RETURN = "Capital return"


# Groups that represent capital gains (as opposed to income events)
CAPITAL_GAINS_GROUPS = [
    TaxableGainGroup.UNLISTED_SHARES.value,
    TaxableGainGroup.LISTED_SHARES.value,
    TaxableGainGroup.OTHER_PROPERTY.value,
]


@dataclass
class TaxRelatedEvent:
    event_type: TaxRelatedEventType
    asset_type: AssetType
    timestamp: int
    asset: str
    quantity: Decimal
    price: Decimal
    platform: str
    currency: str
    fee_value: Decimal
    meta: Optional[dict] = None


@dataclass
class TaxRelatedEventWithMatches:
    """A wrapper around TaxRelatedEvent that includes matching information for tax calculations."""

    event: TaxRelatedEvent
    matched: list[tuple[int, Decimal, str]] = dataclasses.field(
        default_factory=list
    )  # [(index, quantity, rule)]
    remaining_quantity: Decimal = dataclasses.field(init=False)

    def __post_init__(self):
        self.remaining_quantity = self.event.quantity

    @property
    def type(self) -> TaxRelatedEventType:
        return self.event.event_type

    @property
    def asset_type(self) -> Optional[AssetType]:
        return self.event.asset_type

    @property
    def asset(self) -> str:
        return self.event.asset

    @property
    def quantity(self) -> Decimal:
        return self.event.quantity

    @property
    def price(self) -> Decimal:
        return self.event.price

    @property
    def platform(self) -> str:
        return self.event.platform

    @property
    def currency(self) -> str:
        return self.event.currency

    @property
    def fee_value(self) -> Decimal:
        return self.event.fee_value

    @property
    def timestamp(self) -> int:
        return self.event.timestamp
