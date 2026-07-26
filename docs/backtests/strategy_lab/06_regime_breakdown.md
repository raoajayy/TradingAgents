# Strategy Lab — regime breakdown (SO-A)

Each shipped preset's trades grouped by the **market regime at the trade's entry bar**. The regime is classified look-ahead-safely by `analytics.features.classify_regime` over the trailing 60 bars ending at (and including) the entry bar — never a future bar. This answers *"when does this preset actually make its money, and where does it bleed?"* — the input the regime-aware variant (D) uses to switch components.

Regimes: `trending_up`/`trending_down` (strong directional slope + fit), `ranging` (choppy), `high_volatility`/`low_volatility`, `crisis` (extreme vol). Per cell: trades (net P&L $) by regime.

### htf_momentum_v2 · ETH-USD 4h (257 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 42 | 48% | $2,053 | $49 |
| trending_down | 37 | 38% | $-342 | $-9 |
| ranging | 142 | 51% | $11,975 | $84 |
| high_volatility | 36 | 39% | $-891 | $-25 |

### ma_crossover_v1 · ETH-USD 1d (29 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| high_volatility | 17 | 35% | $5,791 | $341 |
| crisis | 12 | 17% | $-1,880 | $-157 |

### mean_reversion_v1 · BTC-USD 4h (168 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 27 | 44% | $795 | $29 |
| trending_down | 31 | 45% | $4,501 | $145 |
| ranging | 97 | 42% | $5,032 | $52 |
| high_volatility | 1 | 0% | $-518 | $-518 |
| low_volatility | 12 | 25% | $-978 | $-82 |

### mean_reversion_v1 · SOL-USD 1d (20 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| high_volatility | 7 | 57% | $2,069 | $296 |
| crisis | 13 | 62% | $6,018 | $463 |

### momentum_v1 · ETH-USD 4h (336 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 52 | 48% | $2,413 | $46 |
| trending_down | 52 | 52% | $8,127 | $156 |
| ranging | 189 | 50% | $18,561 | $98 |
| high_volatility | 43 | 26% | $-14,459 | $-336 |

### momentum_v2 · BTC-USD 4h (94 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 16 | 38% | $233 | $15 |
| trending_down | 15 | 60% | $1,727 | $115 |
| ranging | 53 | 55% | $5,469 | $103 |
| high_volatility | 1 | 100% | $554 | $554 |
| low_volatility | 9 | 33% | $-235 | $-26 |

### regime_momentum_v1 · ETH-USD 1d (61 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| high_volatility | 46 | 50% | $8,367 | $182 |
| crisis | 15 | 40% | $48 | $3 |

### regime_momentum_v1 · ETH-USD 4h (275 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 31 | 48% | $2,947 | $95 |
| trending_down | 28 | 36% | $-585 | $-21 |
| ranging | 215 | 48% | $18,429 | $86 |
| high_volatility | 1 | 100% | $1,068 | $1,068 |

### trend_following_v1 · ETH-USD 1d (45 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| high_volatility | 29 | 48% | $10,703 | $369 |
| crisis | 16 | 38% | $-145 | $-9 |

### trend_following_v1 · SOL-USD 4h (92 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 7 | 14% | $-2,274 | $-325 |
| trending_down | 10 | 40% | $824 | $82 |
| ranging | 56 | 41% | $11,104 | $198 |
| high_volatility | 19 | 53% | $-746 | $-39 |

### trend_following_v2 · ETH-USD 1d (56 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| high_volatility | 38 | 55% | $17,329 | $456 |
| crisis | 18 | 39% | $-152 | $-8 |

### trend_following_v2 · ETH-USD 4h (187 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 28 | 32% | $-2,074 | $-74 |
| trending_down | 18 | 39% | $-1,114 | $-62 |
| ranging | 126 | 44% | $22,318 | $177 |
| high_volatility | 15 | 33% | $-2,152 | $-143 |

### trend_following_v2 · SOL-USD 1d (32 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| high_volatility | 13 | 69% | $1,678 | $129 |
| crisis | 19 | 63% | $2,832 | $149 |

### trend_following_v2 · SOL-USD 4h (137 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 17 | 53% | $-733 | $-43 |
| trending_down | 17 | 35% | $-918 | $-54 |
| ranging | 74 | 46% | $6,698 | $91 |
| high_volatility | 29 | 38% | $308 | $11 |

### volatility_breakout_v1 · ETH-USD 1d (40 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| high_volatility | 27 | 78% | $10,176 | $377 |
| crisis | 13 | 77% | $3,480 | $268 |

### volatility_breakout_v1 · ETH-USD 1h (555 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 63 | 29% | $-1,974 | $-31 |
| trending_down | 41 | 22% | $-2,200 | $-54 |
| ranging | 205 | 26% | $-2,587 | $-13 |
| low_volatility | 246 | 26% | $9,607 | $39 |

### volatility_breakout_v1 · ETH-USD 4h (165 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 26 | 54% | $5,217 | $201 |
| trending_down | 15 | 40% | $842 | $56 |
| ranging | 114 | 60% | $39,638 | $348 |
| high_volatility | 10 | 30% | $536 | $54 |

### volatility_breakout_v1 · SOL-USD 1d (50 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| high_volatility | 21 | 76% | $731 | $35 |
| crisis | 29 | 86% | $2,190 | $76 |

### volatility_breakout_v1 · SOL-USD 4h (108 trades)

| Regime | Trades | Win rate | Total P&L | Avg P&L |
|---|--:|--:|--:|--:|
| trending_up | 11 | 73% | $5,688 | $517 |
| trending_down | 13 | 31% | $2,611 | $201 |
| ranging | 69 | 55% | $20,178 | $292 |
| high_volatility | 15 | 80% | $9,959 | $664 |

**Reading it:** trend/breakout presets should earn the bulk of their P&L in `trending_up`/`trending_down` and give some back in `ranging` — that concentration is the strategy behaving as designed, not overfit. A preset whose profit comes only from a single `high_volatility`/`crisis` cluster is fragile and should be treated with suspicion.
