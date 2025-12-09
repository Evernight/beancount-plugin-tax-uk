"""
Tests for the tax report generation functionality.
"""

import pytest
import pandas as pd
from pathlib import Path
from beancount import loader
from beancount_plugin_tax_uk.spreadsheet_writer import write_tax_report_spreadsheet
from beancount_plugin_tax_uk.calculate_tax import generate_tax_related_events
from beancount_plugin_tax_uk.tax_report import generate_tax_report
from beancount_plugin_tax_uk.rate_converter import BeancountRateConverter
import openpyxl
from openpyxl.utils import get_column_letter


@pytest.mark.parametrize(
    "test_files",
    [
        pytest.param(
            ("trivial_sample.beancount", "test_report_trivial_sample.xlsx", "test_report_trivial_sample.pkl"),
            id="trivial_sample"
        ),
        pytest.param(
            ("sample_KapJI_cgc.beancount", "test_report_KapJI_cgc.xlsx", "test_report_KapJI_cgc.pkl"),
            id="sample_KapJI_cgc"
        ),
        pytest.param(
            ("sample_HS284_Example_3_2021.beancount", "test_report_HS284_Example_3_2021.xlsx", "test_report_HS284_Example_3_2021.pkl"),
            id="sample_HS284_Example_3_2021"
        ),
        pytest.param(
            ("sample_HMRC_bed_and_breakfast.beancount", "test_report_HMRC_bed_and_breakfast.xlsx", "test_report_HMRC_bed_and_breakfast.pkl"),
            id="sample_HMRC_bed_and_breakfast"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/WithAssetEvents.beancount", "report_WithAssetEvents.xlsx", "report_WithAssetEvents.pkl"),
            id="WithAssetEvents"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/Blank.beancount", "report_Blank.xlsx", "report_Blank.pkl"),
            id="Blank"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/SameDayMerge.beancount", "report_SameDayMerge.xlsx", "report_SameDayMerge.pkl"),
            id="SameDayMerge"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/SameDayMergeInterleaved.beancount", "report_SameDayMergeInterleaved.xlsx", "report_SameDayMergeInterleaved.pkl"),
            id="SameDayMergeInterleaved"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/WithAssetEventsMultipleYears.beancount", "report_WithAssetEventsMultipleYears.xlsx", "report_WithAssetEventsMultipleYears.pkl"),
            id="WithAssetEventsMultipleYears"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/HMRCExample1.beancount", "report_HMRCExample1.xlsx", "report_HMRCExample1.pkl"),
            id="HMRCExample1"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/WithAssetEventsSameDay.beancount", "report_WithAssetEventsSameDay.xlsx", "report_WithAssetEventsSameDay.pkl"),
            id="WithAssetEventsSameDay"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/MultipleMatches.beancount", "report_MultipleMatches.xlsx", "report_MultipleMatches.pkl"),
            id="MultipleMatches"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/BuySellAllBuyAgainCapitalReturn.beancount", "report_BuySellAllBuyAgainCapitalReturn.xlsx", "report_BuySellAllBuyAgainCapitalReturn.pkl"),
            id="BuySellAllBuyAgainCapitalReturn"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/Simple.beancount", "report_Simple.xlsx", "report_Simple.pkl"),
            id="Simple"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/CarryLoss.beancount", "report_CarryLoss.xlsx", "report_CarryLoss.pkl"),
            id="CarryLoss"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/WithSplitBB.beancount", "report_WithSplitBB.xlsx", "report_WithSplitBB.pkl"),
            id="WithSplitBB"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/WithSplitS104.beancount", "report_WithSplitS104.xlsx", "report_WithSplitS104.pkl"),
            id="WithSplitS104"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/WithUnsplitBB.beancount", "report_WithUnsplitBB.xlsx", "report_WithUnsplitBB.pkl"),
            id="WithUnsplitBB"
        ),
        pytest.param(
            ("cgtcalc_inputs_beancount/WithUnsplitS104.beancount", "report_WithUnsplitS104.xlsx", "report_WithUnsplitS104.pkl"),
            id="WithUnsplitS104"
        ),
    ],
)
def test_tax_report(capture_output, tmp_path, test_files):
    """Run a tax report test with configurable parameters.

    Args:
        capture_output: Whether to capture output as reference files
        tmp_path: Temporary directory for test output
        test_files: Tuple of (ledger_file, spreadsheet_file, pickle_file)
    """
    ledger_file, spreadsheet_file, pickle_file = test_files

    # Define paths
    data_dir = Path("tests") / "data"
    ledger_path = data_dir / ledger_file
    output_dir = tmp_path
    spreadsheet_path = output_dir / spreadsheet_file
    pickle_path = output_dir / pickle_file
    ref_dir = data_dir / "output"

    # Load ledger file
    entries, errors, options = loader.load_file(str(ledger_path))
    if errors:
        pytest.fail(f"Failed to load test ledger: {errors}")

    # Generate tax related events
    tax_related_events = generate_tax_related_events(entries, options)

    # Generate tax report data
    rate_converter = BeancountRateConverter(entries)
    rows, tax_res, asset_type_mapping = generate_tax_report(
        entries,
        tax_related_events,
        rate_converter=rate_converter,
    )

    # Generate spreadsheet report
    write_tax_report_spreadsheet(
        str(spreadsheet_path), rows, tax_res, asset_type_mapping
    )

    # Store tax results DataFrame
    tax_res.to_pickle(pickle_path)

    if capture_output:
        # Store as reference files
        ref_dir.mkdir(parents=True, exist_ok=True)
        spreadsheet_path.rename(ref_dir / spreadsheet_file)
        pickle_path.rename(ref_dir / pickle_file)
    else:
        # Compare with reference files
        ref_dir = data_dir / "output"
        ref_spreadsheet = ref_dir / spreadsheet_file
        ref_pickle = ref_dir / pickle_file

        # Compare DataFrame
        ref_tax_res = pd.read_pickle(ref_pickle)
        pd.testing.assert_frame_equal(tax_res, ref_tax_res)

        # Compare spreadsheet files
        wb1 = openpyxl.load_workbook(spreadsheet_path)
        wb2 = openpyxl.load_workbook(ref_spreadsheet)
        ws1 = wb1.active
        ws2 = wb2.active

        # Compare cell values
        for row in range(1, ws1.max_row + 1):
            for col in range(1, ws1.max_column + 1):
                cell1 = ws1.cell(row=row, column=col)
                cell2 = ws2.cell(row=row, column=col)
                assert cell1.value == cell2.value, (
                    f"Spreadsheet mismatch at {get_column_letter(col)}{row}: {cell1.value} != {cell2.value}"
                )
