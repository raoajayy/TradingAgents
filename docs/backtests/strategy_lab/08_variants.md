# Strategy Lab — Named Variants A/B/C/D (SO-D)

_Generated 2026-07-26T10:18:35.675097+00:00 · each variant is a portfolio recipe over guard-passing presets (variants.py) · params fixed from the walk-forward presets, no new fitting · vol-target leverage disclosed._

Four risk profiles built **only** from strategies that cleared walk-forward OOS + DSR/PBO. A→C is a deliberate risk ladder; D is regime-gated. OOS Sharpe holds out the last 40% of the timeline (weights fit on the first 60%). Annualization uses each variant's finest component timeframe (mixed-tf variants are 4h-dominated — an approximation, disclosed).

## Headline

| Variant | Profile | Vol target | Leverage | OOS Sharpe | Full Sharpe | CAGR | Max DD | MAR | prob_loss |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **A · Conservative** | conservative | 8% | 2.0x | 1.95 | 2.92 | +5.5% | 0.6% | 9.57 | 0% |
| **B · Balanced (recommended)** | balanced | 15% | 3.0x | 2.41 | 3.16 | +8.3% | 0.9% | 9.30 | 0% |
| **C · Aggressive** | aggressive | 22% | 4.0x | 2.62 | 3.32 | +32.8% | 5.9% | 5.52 | 0% |
| **D · Regime-aware** | regime-aware | 15% | 3.0x | 2.31 | 2.33 | +14.2% | 4.8% | 2.93 | 0% |

> **Leverage caveat:** the vol target is hit by scaling the unlevered blend; the `Leverage` column is that multiplier (capped per variant). Aggressive (C) uses the most — treat its CAGR as leverage-dependent, not free alpha. All figures are backtest estimates on cached crypto history, not a promise of live results.

## A · Conservative

Only the highest-DSR, param-stable daily winners, risk-parity weighted at a low 8% vol target. Prioritizes a smooth equity curve and shallow drawdowns over raw return.

- **Components (4, inverse-vol weighted):** trend_following_v2 ETH-USD/1d (8%), trend_following_v1 ETH-USD/1d (11%), volatility_breakout_v1 ETH-USD/1d (15%), volatility_breakout_v1 SOL-USD/1d (66%)

- **Vol target** 8% → **leverage 2.00x** (unlevered realized vol 0.9%, cap 2x). Annualization 365/yr over 839 blended bars.
- **OOS Sharpe 1.95** · full Sharpe 2.92 · Sortino 7.76 · CAGR +5.5% · max DD 0.6% · MAR 9.57 · total +13.1%.
- **Monte-Carlo** (1000 returns-bootstrap paths): final equity p5 $108,055 / p50 $112,949 / p95 $118,669; max-DD p50 0.8% / p95 1.3%; **prob_loss 0%**.
- _Note: All four components share param-stability 1.0 in the walk-forward run; leverage capped at 2x._

## B · Balanced (recommended)

The six diversified daily-crypto winners across three archetypes (trend, breakout, mean-reversion) on ETH+SOL — the OOS-validated inverse-vol portfolio from 02_portfolio.md — at a 15% vol target. The default recommendation.

- **Components (6, inverse-vol weighted):** trend_following_v2 ETH-USD/1d (6%), volatility_breakout_v1 ETH-USD/1d (12%), trend_following_v1 ETH-USD/1d (9%), mean_reversion_v1 SOL-USD/1d (8%), trend_following_v2 SOL-USD/1d (12%), volatility_breakout_v1 SOL-USD/1d (54%)

- **Vol target** 15% → **leverage 3.00x** (unlevered realized vol 0.8%, cap 3x). Annualization 365/yr over 839 blended bars.
- **OOS Sharpe 2.41** · full Sharpe 3.16 · Sortino 7.86 · CAGR +8.3% · max DD 0.9% · MAR 9.30 · total +20.1%.
- **Monte-Carlo** (1000 returns-bootstrap paths): final equity p5 $112,778 / p50 $120,166 / p95 $128,259; max-DD p50 1.1% / p95 1.8%; **prob_loss 0%**.
- _Note: Reproduces the headline diversified portfolio; inverse-vol chosen because it beat sharpe-tilt out-of-sample._

## C · Aggressive

Pyramiding trend-followers plus 4h chandelier breakouts — higher turnover and a 22% vol target for maximum return, with deeper drawdowns explicitly accepted.

- **Components (5, inverse-vol weighted):** trend_following_v2 ETH-USD/1d (19%), trend_following_v2 SOL-USD/1d (37%), trend_following_v2 ETH-USD/4h (14%), volatility_breakout_v1 ETH-USD/4h (14%), volatility_breakout_v1 SOL-USD/4h (15%)

- **Vol target** 22% → **leverage 4.00x** (unlevered realized vol 2.2%, cap 4x). Annualization 2190/yr over 5336 blended bars.
- **OOS Sharpe 2.62** · full Sharpe 3.32 · Sortino 6.16 · CAGR +32.8% · max DD 5.9% · MAR 5.52 · total +99.6%.
- **Monte-Carlo** (1000 returns-bootstrap paths): final equity p5 $158,097 / p50 $199,854 / p95 $249,855; max-DD p50 4.7% / p95 7.6%; **prob_loss 0%**.
- _Note: Leans on trend_following_v2 (pyramids winners) and the 4h Chandelier breakouts (SO-C1); leverage capped at 4x — this variant is the one most exposed to the leverage caveat._

## D · Regime-aware

Built around regime_momentum_v1 (carries its own market-health gate) plus regime-diverse trend and mean-reversion components — a trend engine for directional regimes, mean-reversion for ranging ones — inverse-vol at 15% vol.

- **Components (4, inverse-vol weighted):** regime_momentum_v1 ETH-USD/1d (18%), regime_momentum_v1 ETH-USD/4h (18%), trend_following_v1 ETH-USD/1d (34%), mean_reversion_v1 SOL-USD/1d (30%)

- **Vol target** 15% → **leverage 3.00x** (unlevered realized vol 1.9%, cap 3x). Annualization 2190/yr over 5336 blended bars.
- **OOS Sharpe 2.31** · full Sharpe 2.33 · Sortino 4.03 · CAGR +14.2% · max DD 4.8% · MAR 2.93 · total +38.2%.
- **Monte-Carlo** (1000 returns-bootstrap paths): final equity p5 $117,926 / p50 $138,021 / p95 $161,516; max-DD p50 4.1% / p95 6.7%; **prob_loss 0%**.
- _Note: Regime-awareness comes from the components' own gates + regime-diverse selection (06_regime_breakdown), NOT a dynamic per-bar switching allocator (recorded as future work). No overclaim._
