"""Fava extension for UK Capital Gains Tax calculations."""

from typing import List, Dict, Any, Tuple
from collections import defaultdict, OrderedDict
from decimal import Decimal
from fava.ext import extension_endpoint
import tempfile
import os
import datetime

import pandas as pd
from fava.ext import FavaExtensionBase

from .tax_report import generate_tax_report
from .calculate_tax import generate_tax_related_events
from .rate_converter import BeancountRateConverter
from .models import TaxableGainGroup, CAPITAL_GAINS_GROUPS
from .spreadsheet_writer import write_tax_report_spreadsheet


class UKTaxPlugin(FavaExtensionBase):
    """Fava extension for UK Capital Gains Tax calculations."""

    report_title = "UK Taxes"

    has_js_module = True

    def __init__(self, ledger, config=None):
        """Initialize the extension."""
        super().__init__(ledger, config)
        self.config = config or {}

    def build_year_summary(
        self, tax_res: pd.DataFrame, year: int
    ) -> Dict[str, Dict[str, Decimal]]:
        """Build summary statistics for a specific tax year.

        Args:
            tax_res: DataFrame containing tax events
            year: Tax year to summarize

        Returns:
            Dictionary containing summary statistics grouped by TaxableGainGroup
        """
        year_data = tax_res[tax_res["year"] == year]

        # Group by classified (TaxableGainGroup value)
        groups = {}
        total_capital_gains = Decimal(0)
        for group_name, group_data in year_data.groupby("classified"):
            total_gains = group_data[group_data["chargeable_gain"] > 0][
                "chargeable_gain"
            ].sum()
            total_losses = -group_data[group_data["chargeable_gain"] < 0][
                "chargeable_gain"
            ].sum()
            total_taxable_gains = total_gains - total_losses

            groups[group_name] = {
                "event_count": group_data["event_count"].sum(),
                "disposal_proceeds": group_data["disposal_proceeds"].sum(),
                "allowable_cost": group_data["allowable_cost"].sum(),
                "total_gains": total_gains,
                "total_losses": total_losses,
                "total_taxable_gains": total_taxable_gains,
            }

            # Add to total capital gains if this is a capital gains group
            if group_name in CAPITAL_GAINS_GROUPS:
                total_capital_gains += total_taxable_gains

        # Add total capital gains to the groups dictionary
        groups["_total_capital_gains"] = {
            "total_taxable_gains": total_capital_gains,
        }

        return groups

    def build_events_list(
        self, tax_res: pd.DataFrame, year: int
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Build list of events for a specific tax year.

        Args:
            tax_res: DataFrame containing tax events
            year: Tax year to get events for

        Returns:
            Dictionary mapping TaxableGainGroup to list of events
        """
        year_data = tax_res[tax_res["year"] == year]

        events = defaultdict(list)
        for _, row in year_data.iterrows():
            events[row["classified"]].append(
                {
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "event_type": row["event_type"],
                    "details": row.get("details", {}),
                    "asset": row["asset"],
                    "proceeds": row["disposal_proceeds"],
                    "cost": row["allowable_cost"],
                    "gain": row["chargeable_gain"],
                }
            )

        # Sort events by date within each group
        for group in events:
            events[group] = sorted(events[group], key=lambda x: x["date"])

        return dict(events)

    def convert_row_for_template(self, row: OrderedDict) -> Dict[str, Any]:
        """Convert a row from the tax report to a format suitable for the template.
        
        Args:
            row: OrderedDict containing row data
            
        Returns:
            Dictionary with serialized values (datetime to string, Decimal to float)
        """
        converted = {}
        for key, value in row.items():
            if isinstance(value, datetime.datetime):
                converted[key] = value.strftime("%Y-%m-%d")
            elif isinstance(value, Decimal):
                converted[key] = float(value)
            elif value is None:
                converted[key] = ""
            elif isinstance(value, str) and value == "":
                converted[key] = ""
            else:
                converted[key] = value
        return converted

    def tax_report(self) -> Dict[str, Any]:
        """Generate the tax report data for the template.

        Returns:
            Dictionary containing report data for all years
        """
        # Generate tax related events from the ledger
        tax_related_events = generate_tax_related_events(
            self.ledger.all_entries, self.ledger.options, verbose=False
        )

        # Use Beancount price directives for rate conversion
        rate_converter = BeancountRateConverter(self.ledger.all_entries)

        # Generate tax report data
        rows, tax_res, asset_type_mapping = generate_tax_report(
            self.ledger.all_entries,
            tax_related_events,
            rate_converter=rate_converter,
            verbose=False,
        )

        # Convert rows to template-friendly format
        converted_rows = [self.convert_row_for_template(row) for row in rows]

        if tax_res.empty:
            return {
                "years": [],
                "summaries": {},
                "events": {},
                "groups": [],
                "rows": converted_rows,
            }

        # Build report data for each year
        years = sorted(tax_res["year"].unique())

        report_data = {
            "years": years,
            "summaries": {},
            "events": {},
            "groups": sorted(
                [g.value for g in TaxableGainGroup]
            ),  # Add list of all groups
            "rows": converted_rows,
        }

        for year in years:
            report_data["summaries"][year] = self.build_year_summary(tax_res, year)
            report_data["events"][year] = self.build_events_list(tax_res, year)

        return report_data

    @extension_endpoint("download_spreadsheet")
    def download_spreadsheet(self) -> Tuple[bytes, int, Dict[str, str]]:
        """Generate and return the tax report spreadsheet file.

        Returns:
            Tuple containing:
            - The file data as bytes
            - HTTP status code (200 for success)
            - Response headers including content type and disposition
        """
        # Generate tax related events from the ledger
        tax_related_events = generate_tax_related_events(
            self.ledger.all_entries, self.ledger.options, verbose=False
        )

        # Use Beancount price directives for rate conversion
        rate_converter = BeancountRateConverter(self.ledger.all_entries)

        # Generate tax report data
        rows, tax_res, asset_type_mapping = generate_tax_report(
            self.ledger.all_entries,
            tax_related_events,
            rate_converter=rate_converter,
            verbose=False,
        )

        # Create a temporary file to store the spreadsheet
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            write_tax_report_spreadsheet(tmp.name, rows, tax_res, asset_type_mapping)

            # Read the file into memory
            with open(tmp.name, "rb") as f:
                file_data = f.read()

            # Clean up the temporary file
            os.unlink(tmp.name)

            # Return the file data with appropriate headers
            headers = {
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "Content-Disposition": "attachment; filename=uk_cgt_report.xlsx",
            }
            return file_data, 200, headers
