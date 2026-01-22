# Generate tax report from Beancount (convert beancount entries into transaction list)

# First standard library imports
import dataclasses
import re
import sys
import time
import pprint
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, List, Dict
import logging

# Then third-party imports
import click
from beancount import loader
from beancount.core.data import Transaction, Custom
from beancount.parser import printer

# Finally local imports
from . import tax_report
from .models import AssetType, TaxRelatedEventType, TaxRelatedEvent
from .rate_converter import BeancountRateConverter, HMRCRateConverter
from .spreadsheet_writer import write_tax_report_spreadsheet

TAG_TO_TYPE = {
    "buy": TaxRelatedEventType.BUY,
    "sell": TaxRelatedEventType.SELL,
    "vest": TaxRelatedEventType.VEST,
    "stock_split": TaxRelatedEventType.STOCK_SPLIT,
    "taxed_income": TaxRelatedEventType.INCOME,
    "rewards_income": TaxRelatedEventType.INCOME,
    "inflation_reward": TaxRelatedEventType.INCOME,
    "staking_income": TaxRelatedEventType.INCOME,
    "ERI": TaxRelatedEventType.ERI,
    "capital_return": TaxRelatedEventType.CAPITAL_RETURN,
    "interest": TaxRelatedEventType.CASH_INCOME,
    "dividend": TaxRelatedEventType.DIVIDEND,
}


def parse_tag_to_type_option(
    raw_value: Optional[str],
) -> Dict[str, Optional[TaxRelatedEventType]]:
    """Parse the `--tag-to-type` option into a mapping of tags to event types."""
    if not raw_value:
        return {}
    mapping: Dict[str, TaxRelatedEventType] = {}
    for entry in raw_value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise click.BadParameter(
                "Each --tag-to-type mapping must be in the form tag=EVENT_TYPE"
            )
        tag, type_name = entry.split("=", 1)
        tag = tag.strip()
        type_name = type_name.strip().upper()
        if not tag:
            raise click.BadParameter(
                "Tag must be provided for --tag-to-type mappings"
            )
        if type_name in {"NONE", "NULL"}:
            mapping[tag] = None
            continue
        if not type_name:
            raise click.BadParameter(
                "Event type must be provided for --tag-to-type mappings"
            )
        try:
            mapping[tag] = TaxRelatedEventType[type_name]
        except KeyError:
            valid_types = ", ".join(t.name for t in TaxRelatedEventType)
            raise click.BadParameter(
                f"Unknown TaxRelatedEventType '{type_name}'. Valid values: {valid_types}"
            )
    return mapping


def get_platform_and_asset_type(
    account: str, platform_mapping: List[tuple[re.Pattern, str, Optional[AssetType]]]
) -> tuple[str, AssetType]:
    """Get the platform and asset type for a given account based on platform mapping rules."""
    result = None
    for pattern, platform_mapped, default_asset_type in platform_mapping:
        if pattern.match(account):
            assert result is None or result == platform_mapped, "multiple matches"
            result = (platform_mapped, default_asset_type or AssetType.STOCKS)
    if result is None:
        return (account, AssetType.STOCKS)
    return result


@dataclass
class TaxConfig:
    """Configuration for tax calculations.

    Attributes:
        platform_mapping: List of (pattern, platform, asset_type) tuples for mapping accounts to platforms
        asset_mapping: Dict mapping asset symbols to (mapped_asset, asset_type)
        tag_mapping: Dict mapping custom tags to standard tax event types
        commission_account_regex: Regex pattern for identifying commission accounts
        income_account_regex: Regex pattern for identifying income accounts
        ignored_account_regex: Regex pattern for identifying ignored accounts
        ignored_currencies: List of currencies to ignore in tax calculations
    """

    platform_mapping: List[tuple[re.Pattern, str, AssetType]]
    asset_mapping: Dict[str, tuple[str, AssetType]]
    tag_mapping: Dict[str, str]
    commission_account_regex: re.Pattern
    income_account_regex: re.Pattern
    ignored_account_regex: re.Pattern
    ignored_currencies: List[str]


def load_tax_config(entries, options, verbose: bool = False) -> TaxConfig:
    """Load tax configuration from Beancount custom entries.

    Processes the following custom entry types:
    - uk-tax-platform-mapping: Maps account patterns to platforms and asset types
      Format: pattern platform [asset_type]
      Example: "Assets:IBKR:.*" "IB" "Stocks"

    - uk-tax-asset-mapping: Maps asset symbols to standardized names and types
      Format: asset mapped_asset [asset_type]
      Example: "ETH" "Ethereum" "CRYPTO"

    - uk-tax-tag-mapping: Maps custom tags to standard tax event types
      Format: tag mapped_tag
      Example: "stock_buy" "buy"

    - uk-tax-config: General configuration options
      Format: config_key value
      Supported keys:
      - commission-account: Regex pattern for commission accounts
      - income-account: Regex pattern for income accounts
      - ignored-account: Regex pattern for ignored accounts
      - ignored-currencies: List of currencies to ignore (in addition to operating currencies)

    Args:
        entries: List of Beancount entries
        options: Beancount options
        verbose: Whether to enable verbose logging

    Returns:
        TaxConfig instance containing all configuration settings
    """
    platform_mapping = []
    asset_mapping = {}
    tag_mapping = {}
    commission_account_regex = re.compile(r"^Expenses:.*:Commissions")
    income_account_regex = re.compile(r"^Income:.*")
    ignored_account_regex = re.compile(r"^Equity:.*")
    ignored_currencies = options["operating_currency"].copy()

    for entry in entries:
        if isinstance(entry, Custom) and entry.type == "uk-tax-platform-mapping":
            assert len(entry.values) >= 2, (
                f"platform mapping must have at least 2 values for {entry}"
            )
            pattern = entry.values[0].value
            platform = entry.values[1].value
            default_asset_type = (
                AssetType[entry.values[2].value.upper()]
                if len(entry.values) > 2
                else AssetType.STOCKS
            )
            platform_mapping.append((re.compile(pattern), platform, default_asset_type))
        elif isinstance(entry, Custom) and entry.type == "uk-tax-asset-mapping":
            assert len(entry.values) >= 2, (
                f"asset mapping must have at least 2 values for {entry}"
            )
            asset = entry.values[0].value
            mapped_asset = entry.values[1].value
            asset_type = (
                AssetType[entry.values[2].value.upper()]
                if len(entry.values) > 2
                else AssetType.STOCKS
            )
            asset_mapping[asset] = (mapped_asset, asset_type)
        elif isinstance(entry, Custom) and entry.type == "uk-tax-tag-mapping":
            assert len(entry.values) == 2, f"tag mapping must have 2 values for {entry}"
            tag = entry.values[0].value
            mapped_tag = entry.values[1].value
            tag_mapping[tag] = mapped_tag
        elif isinstance(entry, Custom) and entry.type == "uk-tax-config":
            assert len(entry.values) >= 2, (
                f"config must have at least 2 values for {entry}"
            )
            config = entry.values[0].value
            if config == "commission-account":
                commission_account_regex = re.compile(entry.values[1].value)
            elif config == "income-account":
                income_account_regex = re.compile(entry.values[1].value)
            elif config == "ignored-account":
                ignored_account_regex = re.compile(entry.values[1].value)
            elif config == "ignored-currencies":
                ignored_currencies.extend([e.value for e in entry.values[1:]])

    config = TaxConfig(
        platform_mapping=platform_mapping,
        asset_mapping=asset_mapping,
        tag_mapping=tag_mapping,
        commission_account_regex=commission_account_regex,
        income_account_regex=income_account_regex,
        ignored_account_regex=ignored_account_regex,
        ignored_currencies=ignored_currencies,
    )

    if verbose:
        logging.debug("Beancount tax plugin config:")
        logging.debug(pprint.pformat(dataclasses.asdict(config)))

    return config


def generate_tax_related_events(
    entries,
    options,
    verbose: bool = False,
    tag_to_type: Optional[Dict[str, TaxRelatedEventType]] = None,
) -> List[TaxRelatedEvent]:
    """Generate tax related events from Beancount entries.

    Args:
        entries: List of Beancount entries
        verbose: Whether to enable verbose debug output

    Returns:
        List of TaxRelatedEvent instances
    """
    # Load plugin configuration from the ledger
    config = load_tax_config(entries, options, verbose)

    transactions = [e for e in entries if isinstance(e, Transaction)]

    effective_tag_to_type: Dict[str, Optional[TaxRelatedEventType]] = TAG_TO_TYPE.copy()
    if tag_to_type:
        effective_tag_to_type.update(tag_to_type)

    def convert_transaction(
        t: Transaction,
        config: TaxConfig,
        verbose: bool = False,
    ) -> Optional[TaxRelatedEvent]:
        tag_found = None
        for tag in t.tags or []:
            mapped_tag = config.tag_mapping.get(tag, tag)
            if mapped_tag in effective_tag_to_type:
                if tag_found:
                    assert False, "two matching tags"
                tag_found = mapped_tag
        if not tag_found:
            return None

        if verbose:
            logging.debug("\nOriginal Beancount Transaction:")
            printer.print_entry(t)
            sys.stdout.flush()

        mapped_type = effective_tag_to_type[tag_found]
        if mapped_type is None:
            return None
        type = mapped_type
        quantity = 0
        commission = 0
        asset = None
        asset_type = None
        platform = None

        price = None
        currency = None

        total_units_incoming = 0
        total_units_outgoing = 0

        for p in t.postings:
            if config.ignored_account_regex.match(p.account):
                pass
            elif config.commission_account_regex.match(p.account):
                commission = p.units.number
            elif config.income_account_regex.match(p.account):
                # Process every type of event where values are defined by the Income posting
                if type in [
                    TaxRelatedEventType.ERI,
                    TaxRelatedEventType.DIVIDEND,
                    TaxRelatedEventType.CASH_INCOME,
                    TaxRelatedEventType.CAPITAL_RETURN,
                ]:
                    assert price is None, "price already set"
                    assert currency is None, "currency already set"
                    price = -p.units.number
                    currency = p.units.currency
                    platform, asset_type = get_platform_and_asset_type(
                        p.account, config.platform_mapping
                    )
                    if type == TaxRelatedEventType.ERI:
                        asset = t.meta["eri_asset"]
                    elif type == TaxRelatedEventType.CAPITAL_RETURN:
                        asset = t.meta["capital_return_asset"]
                    elif type == TaxRelatedEventType.DIVIDEND:
                        asset = t.meta.get("isin", p.account)
                    elif type == TaxRelatedEventType.CASH_INCOME:
                        asset = p.account
            elif p.units is not None and p.units.currency in config.ignored_currencies:
                # Skip operating currency postings, e.g. currency (for most cash transactions)
                pass
            else:
                # General case with posting corresponding to the asset account, e.g.
                #   Assets:Stocks     -1 ASSET {} @ 2000 GBP
                platform, default_asset_type = get_platform_and_asset_type(
                    p.account, config.platform_mapping
                )

                asset, asset_type = config.asset_mapping.get(
                    p.units.currency,
                    (p.units.currency, default_asset_type or AssetType.STOCKS),
                )

                if type == TaxRelatedEventType.SELL:
                    if p.price is None:
                        logging.warning(
                            f"No price for sell transaction {t.meta['filename']}:{t.meta['lineno']}\n"
                            + f"Posting: {p}"
                        )
                        printer.print_entry(t, file=sys.stderr)
                        sys.stderr.flush()
                        continue
                    price = p.price.number
                    currency = p.price.currency
                elif type in [TaxRelatedEventType.BUY, TaxRelatedEventType.VEST]:
                    assert p.cost is not None, (
                        f"No cost for buy or vest posting {p}. Make sure you have a cost directive, or for cash transactions, the currency is an operating currency"
                    )
                    price = p.cost.number
                    currency = p.cost.currency
                elif type == TaxRelatedEventType.INCOME:
                    price = p.cost.number
                    currency = p.cost.currency
                elif type == TaxRelatedEventType.STOCK_SPLIT:
                    # Split operation is just withdrawal of all stock and adding back multiplied amount
                    # Calculate multiplier using that assumption
                    if p.units.number > 0:
                        total_units_incoming += p.units.number
                    else:
                        total_units_outgoing -= p.units.number
                    currency = p.cost.currency
                quantity += p.units.number

        if type == TaxRelatedEventType.STOCK_SPLIT:
            logging.debug(f"Stock split: {asset} {total_units_incoming} / {total_units_outgoing}")
            multiplier = total_units_incoming / total_units_outgoing
            quantity = multiplier  # store multiplier as quantity

        result = TaxRelatedEvent(
            event_type=type,
            asset_type=asset_type,
            timestamp=int(time.mktime(t.date.timetuple()) * 1000),
            asset=asset,
            quantity=Decimal(
                quantity if type != TaxRelatedEventType.SELL else -quantity
            ),
            price=Decimal(price) if price is not None else Decimal(0),
            platform=platform,
            currency=currency,
            fee_value=Decimal(commission),
        )

        if verbose:
            # This will result in more debug output
            result.meta = t.meta

        if verbose:
            logging.debug("\nProcessed Transaction:")
            logging.debug(pprint.pformat(dataclasses.asdict(result)))
        return result

    return list(
        filter(
            lambda t: t,
            [convert_transaction(t, config, verbose) for t in transactions],
        )
    )


def process_ledger(
    ledger_file: str,
    output_file: str,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    hmrc_exchange_rates: Optional[str] = None,
    verbose: bool = False,
    tag_to_type: Optional[Dict[str, TaxRelatedEventType]] = None,
) -> None:
    """Process a Beancount ledger file and generate a tax report.

    Args:
        ledger_file: Path to the Beancount ledger file
        output_file: Path where the spreadsheet report will be saved
        start_year: Start year for tax calculations (default: None, will use earliest transaction year)
        end_year: End year for tax calculations (default: None, will use current tax year)
        hmrc_exchange_rates: Path to HMRC exchange rates directory (optional)
        verbose: Whether to enable verbose debug output
    """
    entries, errors, options = loader.load_file(ledger_file)
    if errors:
        logging.error("Errors loading ledger:")
        printer.print_errors(errors, file=sys.stderr)
        sys.stderr.flush()

    # Generate tax related events
    tax_related_events = generate_tax_related_events(
        entries, options, verbose, tag_to_type=tag_to_type
    )

    # Choose rate converter based on command line parameter
    rate_converter = (
        HMRCRateConverter(rates_path=hmrc_exchange_rates)
        if hmrc_exchange_rates
        else BeancountRateConverter(entries)
    )

    # Generate tax report data
    rows, tax_res, asset_type_mapping = tax_report.generate_tax_report(
        entries,
        tax_related_events,
        rate_converter=rate_converter,
        start_year=start_year,
        end_year=end_year,
        verbose=verbose,
    )

    # Write spreadsheet report
    write_tax_report_spreadsheet(output_file, rows, tax_res, asset_type_mapping)


@click.command()
@click.argument("ledger_file", type=click.Path(exists=True))
@click.argument("output_file", type=click.Path())
@click.option(
    "--start-year",
    type=int,
    default=None,
    help="Start year for tax calculations (default: earliest transaction year)",
)
@click.option(
    "--end-year",
    type=int,
    default=None,
    help="End year for tax calculations (default: current tax year)",
)
@click.option(
    "--hmrc-exchange-rates",
    type=str,
    help="Path to HMRC exchange rates directory. If provided, uses HMRC rates instead of Beancount prices (point to clone of https://github.com/matchilling/hmrc-exchange-rates or identical format)",
)
@click.option("--verbose", is_flag=True, help="Enable verbose debug output")
@click.option(
    "--tag-to-type",
    type=str,
    default=None,
    help="Comma-separated tag=EVENT_TYPE pairs to override tag detection (EVENT_TYPE must be a TaxRelatedEventType member name).",
)
def main(
    ledger_file: str,
    output_file: str,
    start_year: Optional[int],
    end_year: Optional[int],
    hmrc_exchange_rates: Optional[str],
    verbose: bool,
    tag_to_type: Optional[str],
) -> None:
    """Generate UK CGT tax report from Beancount ledger file.

    LEDGER_FILE: Path to the Beancount ledger file
    OUTPUT_FILE: Path where the spreadsheet report will be saved
    """
    # Configure logging based on verbose flag
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO, format="%(message)s"
    )
    tag_to_type_mapping = parse_tag_to_type_option(tag_to_type)
    process_ledger(
        ledger_file,
        output_file,
        start_year,
        end_year,
        hmrc_exchange_rates,
        verbose,
        tag_to_type=tag_to_type_mapping,
    )


if __name__ == "__main__":
    main()
