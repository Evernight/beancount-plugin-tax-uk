import copy
import dataclasses
import datetime
from collections import OrderedDict, defaultdict, namedtuple
from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import Any, Dict, Optional, List, Tuple

import pandas as pd

from .rate_converter import RateConverter, BeancountRateConverter
from .models import (
    AssetType,
    TaxRelatedEventType,
    TaxRule,
    TaxRelatedEventWithMatches,
    TaxableGainGroup,
)


class AssetPool:
    def __init__(self):
        self.transactions = []
        self.total_cost = 0
        self.total_quantity = 0
        self.last_disposal_date = None


@dataclass
class TaxableEventInfo:
    event_type: str = ""
    event_count: int = 1
    date: str = ""
    disposal_proceeds: Decimal = 0
    allowable_cost: Decimal = 0
    chargeable_gain: Decimal = 0
    details: Optional[Dict] = None


TaxYearSummaryKey = namedtuple("TaxYearSummaryKey", ["year", "asset", "asset_type"])

EPS = 1e-8


def get_date_datetime(timestamp: int) -> datetime.datetime:
    item_datetime = datetime.datetime.fromtimestamp(timestamp)
    return datetime.datetime(
        year=item_datetime.year, month=item_datetime.month, day=item_datetime.day
    )


def classify_asset(item: Dict[str, Any]) -> str:
    """Classify an asset based on its type and event type."""
    if item["asset_type"] == AssetType.CFD.value:
        return TaxableGainGroup.UNLISTED_SHARES.value
    if item["asset_type"] == AssetType.CRYPTO.value:
        if item["event_type"] == TaxRelatedEventType.INCOME.value:
            return TaxableGainGroup.OTHER_INCOME.value
        return TaxableGainGroup.OTHER_PROPERTY.value
    if item["event_type"] == TaxRelatedEventType.DIVIDEND.value:
        return TaxableGainGroup.DIVIDENDS.value
    if item["event_type"] == TaxRelatedEventType.CASH_INCOME.value:
        return TaxableGainGroup.OTHER_INCOME.value
    if item["event_type"] == TaxRelatedEventType.SELL.value:
        return TaxableGainGroup.LISTED_SHARES.value
    if item["event_type"] == TaxRelatedEventType.ERI.value:
        return TaxableGainGroup.NOTIONAL_DIVIDENDS.value
    if item["event_type"] == TaxRelatedEventType.CAPITAL_RETURN.value:
        return TaxableGainGroup.CAPITAL_RETURN.value
    logging.warning(
        f"Unhandled asset type {item['asset_type']} and event type {item['event_type']}"
    )
    return str(item["asset_type"]) + "_" + str(item["event_type"])


def match_transactions(
    tr_list: List[TaxRelatedEventWithMatches],
    i: int,
    j: int,
    rule: str,
    stock_splits: List[TaxRelatedEventWithMatches], # List of stock splits for the asset
) -> None:
    """
    Sell transaction is on i-th position, buy transaction is on j-th position (j > i, so buy is later event)

    This may split buy or sell operations and create new items in the list
    """

    stock_split_multiplier = Decimal(1.0)
    for stock_split in stock_splits:
        if tr_list[i].timestamp < stock_split.timestamp < tr_list[j].timestamp:
            # Stock split is between the sell and buy transactions
            stock_split_multiplier *= stock_split.quantity # multiplier stored in "quantity" field

    item_sq = tr_list[i].remaining_quantity
    match_bq = tr_list[j].remaining_quantity / stock_split_multiplier # Normalize to pre-split amount (when sold)

    matched_quantity = min(item_sq, match_bq)

    logging.debug(
        f"> Matching transactions:"
        f"\nSELL: {tr_list[i].asset} | Date: {get_date_datetime(int(tr_list[i].timestamp) / 1000).date()} | "
        f"Type: {tr_list[i].type.value} | Quantity: {tr_list[i].remaining_quantity} | "
        f"Price: {tr_list[i].price} | Platform: {tr_list[i].platform} | "
        f"\nBUY: {tr_list[j].asset} | Date: {get_date_datetime(int(tr_list[j].timestamp) / 1000).date()} | "
        f"Type: {tr_list[j].type.value} | Quantity: {tr_list[j].remaining_quantity} | "
        f"Price: {tr_list[j].price} | Platform: {tr_list[j].platform} | "
        f"\nRule: {rule} | Matched Quantity: {matched_quantity} | "
        f"Stock Split Multiplier: {stock_split_multiplier} | "
        f"Remaining Sell: {tr_list[i].remaining_quantity - matched_quantity} | "
        f"Remaining Buy: {tr_list[j].remaining_quantity - matched_quantity * stock_split_multiplier}\n"
    )

    tr_list[i].matched.append((j, matched_quantity, rule, stock_split_multiplier))
    tr_list[j].matched.append((i, matched_quantity * stock_split_multiplier, rule, stock_split_multiplier))

    tr_list[i].remaining_quantity -= matched_quantity
    tr_list[j].remaining_quantity -= matched_quantity * stock_split_multiplier

def generate_matches(
    input_transactions_list: List[TaxRelatedEventWithMatches],
) -> List[TaxRelatedEventWithMatches]:
    transactions_list = copy.deepcopy(input_transactions_list)

    stock_splits_by_asset = defaultdict(list)
    for item in transactions_list:
        if item.type == TaxRelatedEventType.STOCK_SPLIT:
            stock_splits_by_asset[item.asset].append(item)

    # Passes for rule matching
    # see https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51560
    for match_rule_pass in [TaxRule.SAME_DAY, TaxRule.BED_AND_BREAKFAST]:
        i = 0
        while i < len(transactions_list):
            item = transactions_list[i]

            if (
                item.type != TaxRelatedEventType.SELL
                or item.remaining_quantity < EPS
                or item.asset_type == AssetType.CFD
            ):
                i += 1
                continue

            item_day_datetime = get_date_datetime(int(item.timestamp) / 1000)

            # intuitively you could only iterate from j = i + 1 but need to be able to match the same day buys
            # that even happened before the sell event
            # TODO: it would be more efficient to run it separately for each asset and start not from 0
            j = 0
            while j < len(transactions_list):
                candidate = transactions_list[j]
                if candidate.asset_type == AssetType.CFD:
                    j += 1
                    continue
                # Should Vest event be matcheable?
                if (
                    item.asset != candidate.asset
                    or candidate.type != TaxRelatedEventType.BUY
                    or candidate.remaining_quantity < EPS
                ):
                    j += 1
                    continue
                candidate_day_datetime = get_date_datetime(
                    int(candidate.timestamp) / 1000
                )

                if (
                    match_rule_pass == TaxRule.SAME_DAY
                    and candidate_day_datetime == item_day_datetime
                ):
                    match_transactions(
                        transactions_list,
                        i,
                        j,
                        TaxRule.SAME_DAY.value,
                        stock_splits_by_asset[item.asset],
                    )
                elif (
                    match_rule_pass == TaxRule.BED_AND_BREAKFAST
                    and candidate_day_datetime > item_day_datetime
                    and candidate_day_datetime - item_day_datetime
                    <= datetime.timedelta(days=30)
                ):
                    match_transactions(
                        transactions_list,
                        i,
                        j,
                        TaxRule.BED_AND_BREAKFAST.value,
                        stock_splits_by_asset[item.asset],
                    )

                if item.remaining_quantity < EPS:
                    break

                j += 1

            i += 1

    for i, item in enumerate(transactions_list):
        if item.remaining_quantity >= EPS or not item.matched:
            # Create items even for types of operations where Quantity = 0
            item.matched.append((i, item.remaining_quantity, TaxRule.SECTION_104.value, Decimal(1.0)))
            item.remaining_quantity = 0

    return transactions_list


def _append_year_summary(
    rows: List[OrderedDict],
    rows_by_asset: Dict[str, List[OrderedDict]],
    year_start: datetime.datetime,
    year_end: datetime.datetime,
) -> None:
    """Helper function to append year summary rows to the report.

    Args:
        rows: List to append the summary rows to
        rows_by_asset: Dictionary of asset rows to include in the summary
        year_start: Start of the tax year
        year_end: End of the tax year (optional)
    """

    rows.append(
        OrderedDict(
            {
                "Next year": (
                    year_start.strftime("%b %d %Y")
                    + " - "
                    + (year_end - datetime.timedelta(days=1)).strftime("%b %d %Y")
                )
            }
        )
    )

    # First append all asset transactions for this tax year
    for year_asset in rows_by_asset.keys():
        rows.append(OrderedDict({"AssetSection": year_asset}))
        for asset_row_item in rows_by_asset[year_asset]:
            rows.append(asset_row_item)

    # Then append the tax year summary
    year_dict = {
        "Year (int)": year_start.year,
        "Year end": (year_end - datetime.timedelta(days=1)).strftime("%b %d %Y"),
    }

    rows.append(OrderedDict(year_dict))


def generate_tax_report(
    entries,
    tax_related_events,
    rate_converter: Optional[RateConverter] = None,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    verbose: bool = False,
) -> Tuple[List[OrderedDict], pd.DataFrame, Dict[str, AssetType]]:
    """Generate UK CGT tax report data.

    Args:
        entries: List of Beancount entries containing price directives
        tax_related_events: List of TaxRelatedEvent instances to process
        rate_converter: Rate converter to use for currency conversions (defaults to BeancountRateConverter using provided entries)
        start_year: Start year for tax calculations (default: None, will use earliest transaction year)
        end_year: End year for tax calculations (default: None, will use current tax year)
        verbose: Whether to enable verbose debug output

    Returns:
        Tuple containing:
        - List of row data for the report
        - DataFrame containing tax events data
        - Mapping of assets to their types
    """
    # General rules:
    # https://www.gov.uk/government/publications/shares-and-capital-gains-tax-hs284-self-assessment-helpsheet/hs284-shares-and-capital-gains-tax-2019
    pools = defaultdict(AssetPool)

    if not rate_converter:
        rate_converter = BeancountRateConverter(entries)

    # Tax years: https://www.gov.uk/self-assessment-tax-returns/deadlines
    # Start date is Apr 6th, end date is Apr 5th each year
    if end_year is None:
        current_date = datetime.datetime.now()
        # If current date is before April 6th, use previous year as end_year
        # Otherwise use current year
        end_year = (
            current_date.year
            if current_date.month >= 4 and current_date.day >= 6
            else current_date.year - 1
        )
        if verbose:
            logging.info(f"end_year not specified and set to: {end_year}")

    if start_year is None:
        earliest_timestamp = (
            end_year
            if not tax_related_events
            else min(tr.timestamp for tr in tax_related_events) // 1000
        )
        start_year = datetime.datetime.fromtimestamp(earliest_timestamp).year - 1
        if verbose:
            logging.info(f"start_year not specified and set to: {start_year}")

    tax_year_dividers = [
        datetime.datetime(year=y, month=4, day=6)
        for y in range(start_year, end_year + 2)
    ]

    # Keep asset to asset type mapping consistent for the whole report
    asset_type_mapping: Dict[str, AssetType] = {}

    # Ensure events are sorted by timestamp
    tax_related_events.sort(key=lambda tr: tr.timestamp)

    # Convert TaxRelatedEvents to TaxRelatedEventWithMatches
    transactions_with_matches: List[TaxRelatedEventWithMatches] = [
        TaxRelatedEventWithMatches(event=tr) for tr in tax_related_events
    ]

    transactions_with_matches = generate_matches(transactions_with_matches)

    # Then generate rows
    rows: List[OrderedDict] = []
    rows_by_asset: Dict[str, List[OrderedDict]] = defaultdict(list)
    asset_type_mapping = {}
    taxable_events: Dict[TaxYearSummaryKey, List[TaxableEventInfo]] = defaultdict(list)

    current_tax_year_idx = 0

    for item in transactions_with_matches:
        if item.asset_type:
            asset_type_mapping[item.asset] = item.asset_type

        if item.type == TaxRelatedEventType.BUY and item.asset_type == AssetType.CFD:
            continue

        for match_index, match in enumerate(
            item.matched
        ):  # Debug output for current item
            matched_row_index, match_quantity, match_rule, stock_split_multiplier = match
            cur_datetime = datetime.datetime.fromtimestamp(int(item.timestamp) / 1000)

            if verbose:
                logging.debug(f"Item: {dataclasses.asdict(item)}")
                logging.debug(
                    f"Match: index={match_index}, quantity={match_quantity}, rule={match_rule}"
                )

            # Flush tax year summaries until the current transaction is in the current tax year
            while (
                current_tax_year_idx < len(tax_year_dividers) - 1
                and cur_datetime >= tax_year_dividers[current_tax_year_idx + 1]
            ):
                # Append summary for completed tax year
                _append_year_summary(
                    rows,
                    rows_by_asset,
                    tax_year_dividers[current_tax_year_idx],
                    tax_year_dividers[current_tax_year_idx + 1],
                )

                # Reset for new tax year
                rows_by_asset = defaultdict(list)
                current_tax_year_idx += 1

            # Create row for current transaction
            r = OrderedDict()
            if match_index == 0:
                r["Date"] = cur_datetime

            asset = item.asset
            r["Event"] = item.type.value

            if match_index == 0:
                r["Asset"] = asset
                r["Platform"] = item.platform

            r["Rule"] = match_rule if item.type != TaxRelatedEventType.STOCK_SPLIT else ""

            r["Currency"] = item.currency

            r["GBP to currency rate"] = rate_converter.get_rate(
                int(item.timestamp) / 1000, item.currency
            )
            r["Currency to GBP rate"] = Decimal(1.0) / r["GBP to currency rate"]

            pool = pools[asset]
            if item.type in [
                TaxRelatedEventType.VEST,
                TaxRelatedEventType.BUY,
                TaxRelatedEventType.INCOME,
            ]:
                # These all increase amount in the pool
                r["Buy Quantity"] = Decimal(match_quantity)
                r["Buy Price"] = Decimal(item.price)
                r["Buy Value in Currency"] = (
                    Decimal(item.price) * Decimal(match_quantity)
                    if item.price is not None and item.quantity is not None
                    else None
                )
                r["Buy Value in GBP"] = (
                    r["Buy Value in Currency"] * r["Currency to GBP rate"]
                )

                r["Sell Quantity"] = ""
                r["Sell Price"] = ""
                r["Sell Value in GBP"] = ""

                r["Fee Value in Currency"] = Decimal(item.fee_value) or 0

                fee_in_gbp = r["Fee Value in Currency"] * r["Currency to GBP rate"]

                if match_rule == TaxRule.SECTION_104.value:
                    # Note: fee also forms part of the expenditure in S104 holding
                    # https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51620
                    # https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/1067040/HS284-Example-3-2022.pdf
                    pool.total_cost += r["Buy Value in GBP"] + fee_in_gbp
                    pool.total_quantity += r["Buy Quantity"]

                if item.type == TaxRelatedEventType.INCOME:
                    taxable_events[
                        TaxYearSummaryKey(
                            tax_year_dividers[current_tax_year_idx].year,
                            asset,
                            item.asset_type.value,
                        )
                    ].append(
                        TaxableEventInfo(
                            event_type=TaxRelatedEventType.INCOME.value,
                            date=cur_datetime,
                            disposal_proceeds=r["Buy Value in GBP"],
                            allowable_cost=0,
                            chargeable_gain=r["Buy Value in GBP"],
                        )
                    )
            elif item.asset_type == AssetType.CFD or item.type in [
                TaxRelatedEventType.INCOME,
            ]:
                # TODO: not sure if this (CFD handling) works correctly
                r["Sell Quantity"] = Decimal(item.quantity) if item.quantity else ""
                r["Sell Value in Currency"] = (
                    item.event.profit_in_currency
                    if hasattr(item.event, "profit_in_currency")
                    else None
                )
                r["Sell Value in GBP"] = (
                    r["Sell Value in Currency"] * r["Currency to GBP rate"]
                    if r["Sell Value in Currency"] is not None
                    else None
                )
                r["Chargeable gain"] = r["Sell Value in GBP"]

                taxable_events[
                    TaxYearSummaryKey(
                        tax_year_dividers[current_tax_year_idx].year,
                        asset,
                        item.asset_type.value,
                    )
                ].append(
                    TaxableEventInfo(
                        event_type=item.type.value,
                        date=cur_datetime,
                        disposal_proceeds=r["Sell Value in GBP"],
                        allowable_cost=0,
                        chargeable_gain=r["Chargeable gain"],
                    )
                )

                pool.last_disposal_date = cur_datetime.date()
            elif item.type in [TaxRelatedEventType.ERI]:
                # Increase the cost basis of the asset
                r["Buy Value in Currency"] = Decimal(item.price)
                r["Buy Value in GBP"] = (
                    r["Buy Value in Currency"] * r["Currency to GBP rate"]
                )

                r["Allowable cost"] = r["Buy Value in GBP"]

                taxable_events[
                    TaxYearSummaryKey(
                        tax_year_dividers[current_tax_year_idx].year,
                        asset,
                        item.asset_type.value,
                    )
                ].append(
                    TaxableEventInfo(
                        event_type=item.type.value,
                        date=cur_datetime,
                        disposal_proceeds=r["Buy Value in GBP"],
                        allowable_cost=0,
                        chargeable_gain=r["Buy Value in GBP"],
                    )
                )
                pool.total_cost += r["Allowable cost"]
            elif item.type == TaxRelatedEventType.CAPITAL_RETURN:
                # Reduce the cost basis of the asset
                r["Sell Value in Currency"] = Decimal(item.price)
                r["Sell Value in GBP"] = (
                    r["Sell Value in Currency"] * r["Currency to GBP rate"]
                )
                r["Allowable cost"] = r["Sell Value in GBP"]
                taxable_events[
                    TaxYearSummaryKey(
                        tax_year_dividers[current_tax_year_idx].year,
                        asset,
                        item.asset_type.value,
                    )
                ].append(
                    TaxableEventInfo(
                        event_type=item.type.value,
                        date=cur_datetime,
                        disposal_proceeds=r["Sell Value in GBP"],
                        allowable_cost=0,
                        chargeable_gain=r["Sell Value in GBP"],
                    )
                )
                pool.total_cost -= r["Allowable cost"]
            elif item.type in [
                TaxRelatedEventType.DIVIDEND,
                TaxRelatedEventType.CASH_INCOME,
            ]:
                # Dividends and cash income don't affect the cost basis of the asset
                r["Buy Value in Currency"] = Decimal(item.price)
                r["Buy Value in GBP"] = (
                    r["Buy Value in Currency"] * r["Currency to GBP rate"]
                )
                r["Allowable cost"] = r["Buy Value in GBP"]
                taxable_events[
                    TaxYearSummaryKey(
                        tax_year_dividers[current_tax_year_idx].year,
                        asset,
                        item.asset_type.value,
                    )
                ].append(
                    TaxableEventInfo(
                        event_type=item.type.value,
                        date=cur_datetime,
                        disposal_proceeds=r["Buy Value in GBP"],
                        allowable_cost=0,
                        chargeable_gain=r["Buy Value in GBP"],
                    )
                )
            elif item.type == TaxRelatedEventType.SELL:
                r["Buy Quantity"] = ""
                r["Buy Price"] = ""
                r["Buy Value in GBP"] = ""

                r["Sell Quantity"] = Decimal(match_quantity)
                r["Sell Price"] = Decimal(item.price)
                r["Sell Value in Currency"] = (
                    Decimal(item.price) * Decimal(match_quantity)
                    if item.price is not None and item.quantity is not None
                    else None
                )
                r["Sell Value in GBP"] = (
                    r["Sell Value in Currency"] * r["Currency to GBP rate"]
                )

                r["Fee Value in Currency"] = Decimal(item.fee_value) or 0

                fee_in_gbp = r["Fee Value in Currency"] * r["Currency to GBP rate"]

                if match_rule == TaxRule.SECTION_104.value:
                    # Shares only enter S104 pool if they are not matched, following examples from
                    # https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51560

                    if pool.total_quantity <= 0:
                        # Avoid division by zero
                        assert False, f"ERROR for {item}, empty pool"
                        # logging.error(f"ERROR for {item}, empty pool")
                        # r["Allowable cost"] = 0
                        # r["Error"] = f"ERROR for {item}, empty pool"
                    else:
                        r["Allowable cost"] = (
                            r["Sell Quantity"] / pool.total_quantity * pool.total_cost
                        )

                    pool.total_cost -= r["Allowable cost"]
                    pool.total_quantity -= r["Sell Quantity"]

                    if pool.total_cost < 0 or pool.total_quantity < 0:
                        print(
                            f"ERROR, pool invalid tq={pool.total_quantity}, tc={pool.total_cost} after transaction {item}"
                        )
                        print(f"r={r}")
                else:
                    buy_transaction = transactions_with_matches[matched_row_index]
                    buy_value = Decimal(buy_transaction.price) * Decimal(match_quantity) * Decimal(stock_split_multiplier)
                    buy_ts = int(buy_transaction.timestamp) / 1000
                    buy_rate = rate_converter.get_rate(buy_ts, buy_transaction.currency)
                    buy_value_gbp = (
                        buy_value / buy_rate + buy_transaction.fee_value / buy_rate
                    )

                    r["Allowable cost"] = buy_value_gbp

                r["Chargeable gain"] = (
                    r["Sell Value in GBP"] - r["Allowable cost"] - fee_in_gbp
                )

                taxable_events[
                    TaxYearSummaryKey(
                        tax_year_dividers[current_tax_year_idx].year,
                        asset,
                        item.asset_type.value,
                    )
                ].append(
                    TaxableEventInfo(
                        date=cur_datetime,
                        event_type=TaxRelatedEventType.SELL.value,
                        # Only count one disposal event, not taking into account how many different pools this disposal matched
                        # See: https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51560
                        # "All shares of the same class in the same company disposed of by the same person on the same day
                        # and in the same capacity are also treated as though they were disposed of by a single transaction"
                        event_count=(
                            1
                            if match_index == 0
                            and cur_datetime.date() != pool.last_disposal_date
                            else 0
                        ),
                        disposal_proceeds=r["Sell Value in GBP"],
                        allowable_cost=r["Allowable cost"] + fee_in_gbp,
                        chargeable_gain=r["Chargeable gain"],
                        details={"rule": match_rule},
                    )
                )
                pool.last_disposal_date = cur_datetime.date()

            elif item.type == TaxRelatedEventType.STOCK_SPLIT:
                r["Buy Quantity"] = f'x {item.quantity}'
                # stock split multiplier is stored as quantity
                pool.total_quantity *= Decimal(item.quantity)

            r["Total shares in pool"] = pool.total_quantity
            r["Total cost in pool"] = pool.total_cost

            pool.transactions.append(r)

            rows_by_asset[asset].append(r)

    # Append final tax year summary if there are any remaining transactions
    if rows_by_asset:
        _append_year_summary(
            rows,
            rows_by_asset,
            tax_year_dividers[current_tax_year_idx],
            tax_year_dividers[current_tax_year_idx + 1],
        )

    # Create DataFrame from taxable events
    tax_res = pd.DataFrame(
        [
            # flatten of TaxYearSummaryKey and TaxableEventInfo
            {**key._asdict(), **dataclasses.asdict(item)}
            for key, disposal_list in taxable_events.items()
            for item in disposal_list
        ]
    )
    if not tax_res.empty:
        tax_res = tax_res.sort_values("date")
        tax_res["classified"] = tax_res.apply(classify_asset, axis=1)
    if verbose:
        # Print basic DataFrame info and rows
        logging.info("\n=== Tax Report DataFrame Summary ===")
        logging.info("\nColumn names:")
        logging.info(f"{list(tax_res.columns)}")
        logging.info("\nRows:")
        logging.info(f"\n{tax_res.to_string()}")

    return rows, tax_res, asset_type_mapping
