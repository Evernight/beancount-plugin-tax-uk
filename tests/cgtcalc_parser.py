from datetime import datetime, date
from decimal import Decimal
import os
from typing import List, Tuple, Optional

from beancount import Amount, Booking, CostSpec
from beancount.core import data, amount, position
from beancount.parser.printer import format_entry

DEFAULT_CURRENCY = "GBP"


def parse_date(date_str: str) -> date:
    """Convert DD/MM/YYYY to datetime.date"""
    return datetime.strptime(date_str, "%d/%m/%Y").date()


def parse_line(line: str) -> Tuple[str, date, str, Decimal, Decimal, Optional[Decimal]]:
    """Parse a single line into its components"""
    # Skip empty lines and comments
    if not line.strip() or line.strip().startswith("#"):
        return None

    parts = line.strip().split()

    # Handle SPLIT/UNSPLIT transactions separately since they have different format
    if parts[0] in ("SPLIT", "UNSPLIT"):
        if len(parts) != 4:
            raise ValueError(f"Invalid {parts[0]} line format: {line}")
        ratio = Decimal(parts[3])
        if parts[0] == "UNSPLIT":
            ratio = Decimal("1") / ratio  # Invert ratio for UNSPLIT
        return (
            "SPLIT",  # Both SPLIT and UNSPLIT create a SPLIT transaction type
            parse_date(parts[1]),
            parts[2],  # stock
            ratio,  # split ratio (inverted for UNSPLIT)
            Decimal("0"),  # price (not applicable)
            Decimal("0"),  # fee (not applicable)
        )

    if len(parts) not in (5, 6):  # Allow both 5 or 6 parts (optional fee)
        raise ValueError(f"Invalid line format: {line}")

    # Handle optional fee
    fee = Decimal("0")
    if len(parts) == 6:
        txn_type, date_str, stock, amount, price, fee = parts
    else:
        txn_type, date_str, stock, amount, price = parts

    return (
        txn_type,
        parse_date(date_str),
        stock,
        Decimal(amount),
        Decimal(price),
        Decimal(fee),
    )


def create_buy_transaction(
    txn_date: date, stock: str, units: Decimal, price: Decimal, fee: Decimal
) -> data.Transaction:
    """Create a buy transaction"""
    total_cost = units * price + fee

    meta = data.new_metadata("<stdin>", 0)
    postings = [
        data.Posting(
            "Assets:Stocks",
            amount.Amount(units, stock),
            position.CostSpec(price, None, DEFAULT_CURRENCY, None, None, False),
            None,
            None,
            None,
        ),
        data.Posting(
            "Assets:Cash",
            amount.Amount(-total_cost, DEFAULT_CURRENCY),
            None,
            None,
            None,
            None,
        ),
    ]

    if fee > 0:
        postings.append(
            data.Posting(
                "Expenses:Fees",
                amount.Amount(fee, DEFAULT_CURRENCY),
                None,
                None,
                None,
                None,
            )
        )

    return data.Transaction(
        meta,
        txn_date,
        "*",
        None,
        f"Buy {stock}",
        set(["buy"]),
        data.EMPTY_SET,
        postings,
    )


def create_sell_transaction(
    txn_date: date, stock: str, units: Decimal, price: Decimal, fee: Decimal
) -> data.Transaction:
    """Create a sell transaction"""
    total_proceeds = units * price - fee

    meta = data.new_metadata("<stdin>", 0)
    postings = [
        data.Posting(
            "Assets:Stocks",
            amount.Amount(-units, stock),
            CostSpec(None, None, DEFAULT_CURRENCY, None, None, False),
            amount.Amount(price, DEFAULT_CURRENCY),
            None,
            None,
        ),
        data.Posting(
            "Assets:Cash",
            amount.Amount(total_proceeds, DEFAULT_CURRENCY),
            None,
            None,
            None,
            None,
        ),
        data.Posting(
            "Income:Capital",
            None,  # Let Beancount calculate this
            None,
            None,
            None,
            None,
        ),
    ]

    if fee > 0:
        postings.append(
            data.Posting(
                "Expenses:Fees",
                amount.Amount(fee, DEFAULT_CURRENCY),
                None,
                None,
                None,
                None,
            )
        )

    return data.Transaction(
        meta,
        txn_date,
        "*",
        None,
        f"Sell {stock}",
        set(["sell"]),
        data.EMPTY_SET,
        postings,
    )


def create_dividend_transaction(
    txn_date: date, stock: str, shares: Decimal, total_income: Decimal
) -> data.Transaction:
    """Create a dividend transaction

    Args:
        txn_date: Date of dividend payment
        stock: Stock symbol
        shares: Number of shares that earned the dividend
        total_income: Total dividend payment amount
    """
    # In cgtcalc test cases, dividends are supposed to adjust cost basis of the asset
    # more similar to ERI.
    # TODO: Figure out correct terminology
    meta = data.new_metadata("<stdin>", 0)
    postings = [
        data.Posting(
            "Equity:ERI",
            amount.Amount(total_income, DEFAULT_CURRENCY),
            None,
            None,
            None,
            None,
        ),
        data.Posting(
            "Income:Dividends",
            amount.Amount(-total_income, DEFAULT_CURRENCY),
            None,
            None,
            None,
            None,
        ),
    ]

    meta["eri_asset"] = stock
    return data.Transaction(
        meta,
        txn_date,
        "*",
        None,
        f"Dividend from {stock} ({shares} shares)",
        set(["ERI"]),
        data.EMPTY_SET,
        postings,
    )


def create_capreturn_transaction(
    txn_date: date, stock: str, num_shares: Decimal, amount: Decimal
) -> data.Transaction:
    """Create a capital return transaction"""
    meta = data.new_metadata("<stdin>", 0)
    postings = [
        data.Posting(
            "Assets:Cash", Amount(amount, DEFAULT_CURRENCY), None, None, None, None
        ),
        data.Posting(
            "Income:Capital", Amount(-amount, DEFAULT_CURRENCY), None, None, None, None
        ),
    ]
    meta["capital_return_asset"] = stock
    return data.Transaction(
        meta,
        txn_date,
        "*",
        None,
        f"Capital Return from {stock}",
        set(["capital_return"]),
        data.EMPTY_SET,
        postings,
    )


def create_open_directive(account: str, open_date: date) -> data.Open:
    """Create an Open directive for an account"""
    return data.Open(
        data.new_metadata("<stdin>", 0), open_date, account, None, Booking.FIFO
    )


def create_split_transaction(
    txn_date: date, stock: str, ratio: Decimal
) -> data.Transaction:
    """Create a stock split transaction"""
    meta = data.new_metadata("<stdin>", 0)
    # This is incorrect and has to be adjusted manually for now
    postings = [
        data.Posting(
            "Assets:Stocks",
            amount.Amount(Decimal("0"), stock),  # Zero amount since it's just a split
            None,
            None,
            None,
            None,
        ),
    ]

    return data.Transaction(
        meta,
        txn_date,
        "*",
        None,
        f"Stock split {stock} {ratio}:1",
        set(["stock_split"]),
        data.EMPTY_SET,
        postings,
    )


def create_commodity_directive(commodity: str, open_date: date) -> data.Commodity:
    """Create a Commodity directive"""
    return data.Commodity(data.new_metadata("<stdin>", 0), open_date, commodity)


def create_beancount_entries(
    input_lines: List[str], title: Optional[str] = None
) -> Tuple[List[data.Directive], List[str]]:
    """Convert input lines to Beancount directives"""
    entries = []
    additional_lines = []
    stocks = set()  # Track unique stock symbols

    # Add title as a comment if provided
    if title:
        additional_lines.append(f'option "title" "{title}"')

    # Add account open directives
    open_date = date(1970, 1, 1)
    for account in [
        "Assets:Stocks",
        "Assets:Cash",
        "Income:Dividends",
        "Income:Capital",
        "Expenses:Fees",
        "Equity:ERI",
        "Income:ERI",
    ]:
        entries.append(create_open_directive(account, open_date))

    additional_lines.append(f'option "operating_currency" "{DEFAULT_CURRENCY}"')

    entries.append(
        data.Custom(
            data.new_metadata("<stdin>", 0),
            date(1970, 1, 1),
            "uk-tax-config",
            [("ignored-currencies", str), (DEFAULT_CURRENCY, str)],
        )
    )
    entries.append(
        data.Custom(
            data.new_metadata("<stdin>", 0),
            date(2010, 1, 1),
            "fava-extension",
            [("beancount_plugin_tax_uk.fava_extension", str), ("{}", str)],
        )
    )
    entries.append(
        data.Custom(
            data.new_metadata("<stdin>", 0),
            date(2010, 1, 1),
            "uk-tax-config",
            [("commission-account", str), ("Expenses:Fees", str)],
        )
    )
    # First pass: collect all unique stock symbols
    for line in input_lines:
        parsed = parse_line(line)
        if parsed is None:  # Skip empty lines and comments
            continue

        _, _, stock, _, _, _ = parsed
        stock = stock.upper()
        stocks.add(stock)

    # Add commodity declarations for all stocks
    for stock in sorted(stocks):  # Sort for consistent output
        entries.append(create_commodity_directive(stock, open_date))

    # Add commodity declaration for the default currency
    entries.append(create_commodity_directive(DEFAULT_CURRENCY, open_date))

    # Second pass: process all transactions
    for line in input_lines:
        parsed = parse_line(line)
        if parsed is None:  # Skip empty lines and comments
            continue

        txn_type, txn_date, stock, amount_val, price, fee = parsed
        stock = stock.upper()

        if txn_type == "BUY":
            entries.append(
                create_buy_transaction(txn_date, stock, amount_val, price, fee)
            )
        elif txn_type == "SELL":
            entries.append(
                create_sell_transaction(txn_date, stock, amount_val, price, fee)
            )
        elif txn_type == "DIVIDEND":
            entries.append(
                create_dividend_transaction(txn_date, stock, amount_val, price)
            )
        elif txn_type == "CAPRETURN":
            num_shares = Decimal(parsed[3])
            amount_val = Decimal(parsed[4])
            entries.append(
                create_capreturn_transaction(txn_date, stock, num_shares, amount_val)
            )
        elif txn_type == "SPLIT":
            entries.append(create_split_transaction(txn_date, stock, amount_val))

    # Sort entries by date and #buy before #sell
    entries.sort(
        key=lambda x: (
            x.date,
            next(iter(x.tags)) if hasattr(x, "tags") and x.tags else "",
        )
    )

    return entries, additional_lines


def parse_file(filename: str) -> str:
    """Parse a file and return formatted Beancount entries"""
    with open(filename, "r") as f:
        lines = f.readlines()

    entries, additional_lines = create_beancount_entries(
        lines, title=os.path.basename(filename)
    )
    return (
        "\n".join(additional_lines)
        + "\n"
        + "\n".join(format_entry(entry) for entry in entries)
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python cgtcalc_parser.py <input_file>")
        sys.exit(1)

    print(parse_file(sys.argv[1]))
