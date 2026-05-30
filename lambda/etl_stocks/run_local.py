"""Local test runner — reads from sample_data, writes Stock - Processed.xlsx."""
import logging
import sys
from pathlib import Path

# Allow running from any working directory
sys.path.insert(0, str(Path(__file__).parent))
from process import process_stock_file

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

_SAMPLE = Path(__file__).parent.parent / 'etl_sales' / 'sample_data'
_DEFAULT_SRC = _SAMPLE / 'Current Stock Balances12-5-2026(21.42.18).xlsx'
_DEFAULT_DST = _SAMPLE / 'Stock - Processed.xlsx'


def _find_rates_file() -> str | None:
    matches = sorted(_SAMPLE.glob('Product Masters With Rates*.xlsx'))
    return str(matches[-1]) if matches else None


if __name__ == '__main__':
    src = (sys.argv[1] or str(_DEFAULT_SRC)) if len(sys.argv) > 1 else str(_DEFAULT_SRC)
    dst = (sys.argv[2] or str(_DEFAULT_DST)) if len(sys.argv) > 2 else str(_DEFAULT_DST)
    rates = _find_rates_file()
    if rates:
        print(f"Using rates file: {rates}")
    n = process_stock_file(src, dst, rates_path=rates)
    print(f"Done. {n} rows written to {dst}")
