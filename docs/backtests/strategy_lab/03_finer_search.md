# Strategy Lab — Finer Walk-Forward Search (Phase 6)

_Generated 2026-07-25T07:37:46.500351+00:00 · genetic search (pop 12 × 5 gens) over the FULL declared param ranges on the top cells vs the coarse-grid presets. A finer search evaluates more configs, so DSR is deflated harder — a promotion must beat both the preset OOS Sharpe AND the guard bar._

| Cell | preset OOS | finer OOS | finer DSR | finer PBO | guard | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| trend_following_v2@ETH-USD/1d | 0.097 | 0.152 | 0.474 | 0.01 | — | better,not-robust |
| trend_following_v1@ETH-USD/1d | 0.075 | 0.159 | 0.759 | 0.00 | ✅ | PROMOTE |
| volatility_breakout_v1@ETH-USD/1d | 0.083 | 0.121 | 0.849 | 0.00 | ✅ | PROMOTE |
| regime_momentum_v1@ETH-USD/4h | 0.040 | -0.034 | 0.780 | 0.17 | — | no gain |
| trend_following_v2@ETH-USD/4h | 0.037 | 0.073 | 0.711 | 0.27 | ✅ | PROMOTE |
| volatility_breakout_v1@ETH-USD/4h | 0.034 | 0.090 | 0.973 | 0.10 | ✅ | PROMOTE |
| trend_following_v2@SOL-USD/1d | 0.058 | 0.145 | 0.315 | 0.00 | — | better,not-robust |
| volatility_breakout_v1@SOL-USD/1d | 0.030 | 0.123 | 0.919 | 0.00 | ✅ | PROMOTE |
| mean_reversion_v1@SOL-USD/1d | 0.019 | -0.007 | 0.667 | 0.40 | — | no gain |

## Candidate promotions (better OOS AND guard-passing)

- **trend_following_v1@ETH-USD/1d** → OOS 0.159 (DSR 0.759, PBO 0.00, share 0.5): `{'allow_short': 'yes', 'donchian_period': 22, 'risk_pct': 1.1025312026553822, 'stop_atr_mult': 3.0733822620016227, 'trail_pct': 0.012290127489411473}`
- **volatility_breakout_v1@ETH-USD/1d** → OOS 0.121 (DSR 0.849, PBO 0.00, share 1.0): `{'allow_short': 'yes', 'lookback': 34, 'risk_pct': 1.3546502028310872, 'squeeze_pct': 0.14233789737754912, 'stop_atr_mult': 3.548535276531993, 'trail_pct': 0.012753098473019819}`
- **trend_following_v2@ETH-USD/4h** → OOS 0.073 (DSR 0.711, PBO 0.27, share 0.25): `{'add_atr_mult': 0.7290035187907319, 'allow_short': 'yes', 'donchian_period': 13, 'max_adds': 2, 'risk_pct': 0.7719802065464597, 'stop_atr_mult': 3.2395821669211085, 'trail_pct': 0.010828444469894649}`
- **volatility_breakout_v1@ETH-USD/4h** → OOS 0.090 (DSR 0.973, PBO 0.10, share 0.5): `{'allow_short': 'yes', 'lookback': 35, 'risk_pct': 0.17379299688103633, 'squeeze_pct': 0.10788730469565734, 'stop_atr_mult': 3.548535276531993, 'trail_pct': 0.012753098473019819}`
- **volatility_breakout_v1@SOL-USD/1d** → OOS 0.123 (DSR 0.919, PBO 0.00, share 1.0): `{'allow_short': 'yes', 'lookback': 10, 'risk_pct': 0.4419642406460771, 'squeeze_pct': 0.10788730469565734, 'stop_atr_mult': 3.548535276531993, 'trail_pct': 0.01567961396989384}`

