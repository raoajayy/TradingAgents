# Strategy Lab — Monte-Carlo robustness (SO-A)

Bootstrap resampling (1000 paths, seed 7) of each shipped preset's realized trade P&Ls, starting from $100,000. `prob_loss` is the fraction of resampled paths ending **below** the starting stake — the single most useful robustness number: a preset with a real edge but an unlucky trade order should still rarely lose money across resamples.

Source: one full-window backtest per preset on cached bars (`scripts/pro_lab_robustness.py`). Presets with <2 trades are omitted (Monte-Carlo undefined).

| Strategy | Sym | TF | Trades | p5 equity | p50 equity | p95 equity | maxDD p50 | maxDD p95 | prob_loss |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|
| htf_momentum_v2 | ETH-USD | 4h | 257 | $98,589 | $112,555 | $128,101 | 5.8% | 11.0% | 7.4% |
| ma_crossover_v1 | ETH-USD | 1d | 29 | $95,557 | $103,364 | $114,192 | 2.9% | 5.9% | 25.9% |
| mean_reversion_v1 | BTC-USD | 4h | 168 | $99,064 | $108,524 | $118,051 | 3.3% | 6.5% | 6.5% |
| mean_reversion_v1 | SOL-USD | 1d | 20 | $99,615 | $108,234 | $116,849 | 2.9% | 5.5% | 5.8% |
| momentum_v1 | ETH-USD | 4h | 336 | $97,925 | $115,403 | $134,507 | 7.2% | 14.3% | 8.2% |
| momentum_v2 | BTC-USD | 4h | 94 | $101,314 | $107,700 | $114,402 | 2.2% | 4.3% | 2.1% |
| regime_momentum_v1 | ETH-USD | 1d | 61 | $92,455 | $108,110 | $123,074 | 6.4% | 13.4% | 19.7% |
| regime_momentum_v1 | ETH-USD | 4h | 275 | $104,650 | $121,900 | $137,670 | 5.2% | 9.5% | 1.8% |
| trend_following_v1 | ETH-USD | 1d | 45 | $102,101 | $110,767 | $120,765 | 1.8% | 3.4% | 2.2% |
| trend_following_v1 | SOL-USD | 4h | 92 | $97,452 | $109,096 | $122,069 | 4.3% | 8.4% | 10.7% |
| trend_following_v2 | ETH-USD | 1d | 56 | $107,020 | $117,243 | $128,163 | 1.7% | 3.2% | 0.2% |
| trend_following_v2 | ETH-USD | 4h | 187 | $102,038 | $116,404 | $132,663 | 4.2% | 7.9% | 2.9% |
| trend_following_v2 | SOL-USD | 1d | 32 | $100,897 | $104,416 | $108,608 | 1.0% | 2.2% | 1.9% |
| trend_following_v2 | SOL-USD | 4h | 137 | $95,691 | $105,107 | $115,871 | 4.0% | 8.3% | 19.9% |
| volatility_breakout_v1 | ETH-USD | 1d | 40 | $109,230 | $113,616 | $118,674 | 0.1% | 0.3% | 0.0% |
| volatility_breakout_v1 | ETH-USD | 1h | 555 | $89,099 | $103,279 | $117,707 | 8.0% | 15.5% | 37.3% |
| volatility_breakout_v1 | ETH-USD | 4h | 165 | $128,880 | $144,958 | $163,455 | 2.0% | 3.5% | 0.0% |
| volatility_breakout_v1 | SOL-USD | 1d | 50 | $102,128 | $102,905 | $103,836 | 0.0% | 0.1% | 0.0% |
| volatility_breakout_v1 | SOL-USD | 4h | 108 | $122,212 | $137,975 | $156,915 | 2.0% | 3.5% | 0.0% |

**Reading it:** a low `prob_loss` with a p5 equity above the stake is the robust signature; a high `prob_loss` warns that the historical profit leaned on trade *ordering* (luck), not a repeatable edge. These are in-sample resamples — they bound trade-sequence risk, not regime-change risk (see the regime breakdown for that).
