# Strategy Lab — Data Inventory (Phase 0)

_Generated 2026-07-24T19:29:41.546168+00:00 · requested up to 30,000 bars/cell · a cell is **analyzable** only with ≥ 203 real bars (engine warm-up 60 + ≥2 walk-forward windows)._

**23 / 28 cells are analyzable.** Non-analyzable cells are recorded as data-limited gaps — no result will be fabricated for them.

| Symbol | TF | Real bars | First | Last | Truncated | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| BTC-USD | 5m | 30000 | 2026-04-11 | 2026-07-24 | no | ✅ analyzable |
| BTC-USD | 15m | 30000 | 2025-09-15 | 2026-07-24 | no | ✅ analyzable |
| BTC-USD | 30m | 30000 | 2024-11-06 | 2026-07-24 | no | ✅ analyzable |
| BTC-USD | 1h | 21921 | 2024-01-23 | 2026-07-24 | no | ✅ analyzable |
| BTC-USD | 4h | 5630 | 2023-12-29 | 2026-07-24 | no | ✅ analyzable |
| BTC-USD | 1d | 939 | 2023-12-29 | 2026-07-24 | no | ✅ analyzable |
| BTC-USD | 1w | 135 | 2023-12-25 | 2026-07-20 | no | ⚠️ insufficient-data |
| ETH-USD | 5m | 30000 | 2026-04-11 | 2026-07-24 | no | ✅ analyzable |
| ETH-USD | 15m | 30000 | 2025-09-15 | 2026-07-24 | no | ✅ analyzable |
| ETH-USD | 30m | 30000 | 2024-11-06 | 2026-07-24 | no | ✅ analyzable |
| ETH-USD | 1h | 21588 | 2024-02-06 | 2026-07-24 | no | ✅ analyzable |
| ETH-USD | 4h | 5397 | 2024-02-06 | 2026-07-24 | no | ✅ analyzable |
| ETH-USD | 1d | 900 | 2024-02-06 | 2026-07-24 | no | ✅ analyzable |
| ETH-USD | 1w | 129 | 2024-02-05 | 2026-07-20 | no | ⚠️ insufficient-data |
| SOL-USD | 5m | 30000 | 2026-04-11 | 2026-07-24 | no | ✅ analyzable |
| SOL-USD | 15m | 30000 | 2025-09-15 | 2026-07-24 | no | ✅ analyzable |
| SOL-USD | 30m | 30000 | 2024-11-06 | 2026-07-24 | no | ✅ analyzable |
| SOL-USD | 1h | 20094 | 2024-04-08 | 2026-07-24 | no | ✅ analyzable |
| SOL-USD | 4h | 5024 | 2024-04-08 | 2026-07-24 | no | ✅ analyzable |
| SOL-USD | 1d | 838 | 2024-04-08 | 2026-07-24 | no | ✅ analyzable |
| SOL-USD | 1w | 120 | 2024-04-08 | 2026-07-20 | no | ⚠️ insufficient-data |
| XAUUSD | 5m | 28305 | 2026-04-17 | 2026-07-24 | no | ✅ analyzable |
| XAUUSD | 15m | 9435 | 2026-04-17 | 2026-07-24 | no | ✅ analyzable |
| XAUUSD | 30m | 4718 | 2026-04-17 | 2026-07-24 | no | ✅ analyzable |
| XAUUSD | 1h | 2360 | 2026-04-17 | 2026-07-24 | no | ✅ analyzable |
| XAUUSD | 4h | 590 | 2026-04-17 | 2026-07-24 | no | ✅ analyzable |
| XAUUSD | 1d | 99 | 2026-04-17 | 2026-07-24 | no | ⚠️ insufficient-data |
| XAUUSD | 1w | 15 | 2026-04-13 | 2026-07-20 | no | ⚠️ insufficient-data |

## Notes

- Raw bars cached under `data/bars_cache/<symbol>_<tf>.jsonl` (git-ignored); the Strategy Lab reads the cache, not the vendor, so runs are offline + reproducible.
- Depth is whatever the live vendor actually served (Delta/Binance for crypto, Delta/OANDA/yfinance for gold), paged 1000/request.
- Re-run this probe to refresh the cache with newer history.
