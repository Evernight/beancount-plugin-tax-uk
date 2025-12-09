from collections import OrderedDict
from typing import Dict, List

import pandas as pd
import xlsxwriter

from .models import AssetType, CAPITAL_GAINS_GROUPS

# Keys are names, values are widths
SPREADSHEET_COLUMNS = {
    "Date": 12,
    "Asset": 18,
    "Platform": 13,
    "Event": 9,
    "Rule": 5,
    "Currency": 5,
    "Buy Quantity": 10,
    "Buy Price": 10,
    "Buy Value in Currency": 10,
    "Buy Value in GBP": 10,
    "Sell Quantity": 10,
    "Sell Price": 10,
    "Sell Value in Currency": 10,
    "Sell Value in GBP": 10,
    "Fee Value in Currency": 10,
    "Total shares in pool": 10,
    "Total cost in pool": 10,
    "Allowable cost": 10,
    "Chargeable gain": 10,
    "Currency to GBP rate": 10,
    "GBP to currency rate": 10,
}

CURRENCIES = ["GBP", "USD", "EUR", "Other"]


def write_tax_report_spreadsheet(
    output_filename: str,
    rows: List[OrderedDict],
    tax_res: pd.DataFrame,
    asset_type_mapping: Dict[str, AssetType],
) -> None:
    """Write tax report data to a spreadsheet workbook.

    Args:
        output_filename: Path to save the spreadsheet file
        rows: List of row data to write
        taxable_events: Dictionary of tax events
        asset_type_mapping: Mapping of assets to their types
    """
    workbook = xlsxwriter.Workbook(output_filename)
    worksheet = workbook.add_worksheet()

    # Set up formats
    title_format = workbook.add_format()
    title_format.set_bold()
    title_format.set_text_wrap()

    capital_gains_format = workbook.add_format({"bg_color": "#d9ead3"})
    capital_gains_format.set_bold()

    year_divider_format = workbook.add_format({"bg_color": "#add8e6"})
    year_divider_format.set_bold()

    capital_gains_format_gbp = workbook.add_format({"bg_color": "#d9ead3"})
    capital_gains_format_gbp.set_bold()
    capital_gains_format_gbp.set_num_format("£#,##0.00")

    # Write column headers
    for ind, key in enumerate(SPREADSHEET_COLUMNS.keys()):
        worksheet.write(0, ind, key, title_format)
        worksheet.set_column(ind, ind, SPREADSHEET_COLUMNS[key])

    worksheet.set_row(0, 40)
    worksheet.freeze_panes(1, 0)

    # Create formats for different currencies
    formats = {}
    sell_formats = {}
    for key in SPREADSHEET_COLUMNS.keys():
        for currency in CURRENCIES:
            key_format = workbook.add_format()
            sell_format = workbook.add_format({"bg_color": "#fff2cc"})

            if key in [
                "Buy Value in Currency",
                "Sell Value in Currency",
                "Fee Value in Currency",
                "Buy Price",
                "Sell Price",
            ]:
                if currency == "USD":
                    key_format.set_num_format("$#,##0.00")
                    sell_format.set_num_format("$#,##0.00")
                elif currency == "GBP":
                    key_format.set_num_format("£#,##0.00")
                    sell_format.set_num_format("£#,##0.00")
                elif currency == "EUR":
                    key_format.set_num_format("€#,##0.00")
                    sell_format.set_num_format("€#,##0.00")
            elif key in [
                "Buy Value in GBP",
                "Sell Value in GBP",
                "Allowable cost",
                "Chargeable gain",
                "Total cost in pool",
            ]:
                key_format.set_num_format("£#,##0.00")
                sell_format.set_num_format("£#,##0.00")
            formats[key + "_" + currency] = key_format
            sell_formats[key + "_" + currency] = sell_format

    # Write data rows
    row_ind = 1
    for r in rows:
        if "Year (int)" in r:
            worksheet.write(
                row_ind + 1, 0, f"Summary for tax year ending {r['Year end']}"
            )
            worksheet.set_row(row_ind + 1, 20, year_divider_format)
            row_ind += 3

            for event_class, res in tax_res[
                (tax_res["year"] == r["Year (int)"])
            ].groupby("classified"):
                worksheet.write(row_ind, 0, event_class)
                worksheet.set_row(row_ind, 20, year_divider_format)
                row_ind += 1

                worksheet.write(
                    row_ind, 0, "Number of taxable events", capital_gains_format
                )
                worksheet.write(
                    row_ind + 1, 0, res["event_count"].sum(), capital_gains_format
                )
                row_ind += 2

                if event_class in CAPITAL_GAINS_GROUPS:
                    worksheet.write(
                        row_ind, 0, "Disposal proceeds", capital_gains_format
                    )
                    if event_class != "Unlisted shares and securities":
                        worksheet.write(
                            row_ind + 1,
                            0,
                            res["disposal_proceeds"].sum(),
                            capital_gains_format_gbp,
                        )
                    else:
                        worksheet.write(
                            row_ind + 1,
                            0,
                            res[res["chargeable_gain"] > 0]["chargeable_gain"].sum(),
                            capital_gains_format_gbp,
                        )
                    row_ind += 2

                if event_class in CAPITAL_GAINS_GROUPS:
                    worksheet.write(row_ind, 0, "Allowable costs", capital_gains_format)
                    if event_class != "Unlisted shares and securities":
                        worksheet.write(
                            row_ind + 1,
                            0,
                            res["allowable_cost"].sum(),
                            capital_gains_format_gbp,
                        )
                    else:
                        worksheet.write(
                            row_ind + 1,
                            0,
                            -res[res["chargeable_gain"] < 0]["chargeable_gain"].sum(),
                            capital_gains_format_gbp,
                        )
                    row_ind += 2

                worksheet.write(row_ind, 0, "Total year gains", capital_gains_format)
                worksheet.write(
                    row_ind + 1,
                    0,
                    res[res["chargeable_gain"] > 0]["chargeable_gain"].sum(),
                    capital_gains_format_gbp,
                )
                row_ind += 2

                if event_class in CAPITAL_GAINS_GROUPS:
                    worksheet.write(
                        row_ind, 0, "Total year losses", capital_gains_format
                    )
                    worksheet.write(
                        row_ind + 1,
                        0,
                        -res[res["chargeable_gain"] < 0]["chargeable_gain"].sum(),
                        capital_gains_format_gbp,
                    )
                    row_ind += 2

                row_ind += 1

        elif "Next year" in r:
            worksheet.write(row_ind, 0, "Tax year: " + r["Next year"])
            worksheet.set_row(row_ind, 20, year_divider_format)
            row_ind += 1

        elif "AssetSection" in r:
            worksheet.write(row_ind + 1, 0, "Asset", capital_gains_format)
            worksheet.write(row_ind + 1, 1, r["AssetSection"], capital_gains_format)
            if r["AssetSection"] in asset_type_mapping:
                worksheet.write(
                    row_ind + 1,
                    2,
                    asset_type_mapping[r["AssetSection"]].value,
                    capital_gains_format,
                )
            row_ind += 2
        else:
            # General case
            for key, value in r.items():
                if key == "Date":
                    value = value.strftime("%d/%m/%Y")

                # Find key index in SPREADSHEET_COLUMNS
                if key not in SPREADSHEET_COLUMNS:
                    continue
                col_ind = list(SPREADSHEET_COLUMNS.keys()).index(key)
                currency = r["Currency"]

                if "Event" in r and r["Event"] == "Sell":
                    if key + "_" + currency in sell_formats:
                        worksheet.write(
                            row_ind, col_ind, value, sell_formats[key + "_" + currency]
                        )
                    else:
                        if key + "_Other" in sell_formats:
                            worksheet.write(
                                row_ind, col_ind, value, sell_formats[key + "_Other"]
                            )
                        else:
                            worksheet.write(row_ind, col_ind, value)
                else:
                    if key + "_" + currency in formats:
                        worksheet.write(
                            row_ind, col_ind, value, formats[key + "_" + currency]
                        )
                    else:
                        if key + "_Other" in formats:
                            worksheet.write(
                                row_ind, col_ind, value, formats[key + "_Other"]
                            )
                        else:
                            worksheet.write(row_ind, col_ind, value)

            row_ind += 1

    workbook.close()
