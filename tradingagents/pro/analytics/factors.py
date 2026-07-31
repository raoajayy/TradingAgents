"""Safe formulaic-factor evaluation over bar history (roadmap P3-03).

An LLM proposes factor formulas as *text*; this module is the only thing
that ever turns that text into numbers, so it must be safe against a
hostile or confused model. There is NO ``exec``/``eval``: expressions are
parsed with ``ast.parse`` into a tiny validated expression language —
arithmetic, comparisons, and a fixed whitelist of time-series functions
over named columns. Anything else (attributes, subscripts, lambdas,
imports, dunder names, unknown functions, non-constant windows, negative
lags) is rejected at parse time with a reason the mining loop can feed
back to the proposer.

Causality is a parse-time property: every whitelisted function looks only
backward (rolling windows, positive shifts), and ``delay``/``delta`` with
a negative lag — the classic look-ahead smuggle — is refused outright.
Hence a parsed ``FactorExpr`` evaluated on bars up to *t* uses only data
at or before *t*, and the OOS evaluator below can trust the purged
K-fold splits from ``analytics.validation`` (P2-02).
"""

from __future__ import annotations

import ast
import math

import numpy as np
import pandas as pd

from tradingagents.pro.analytics.validation import purged_kfold_splits

BASE_COLUMNS = ("open", "high", "low", "close", "volume")
_MAX_EXPR_LEN = 400
_MAX_WINDOW = 5000

# name -> (n_args, doc). Argument shapes are validated at parse time:
# series arguments are sub-expressions, window/lag arguments must be
# literal non-negative integer constants.
FUNCTIONS: dict[str, tuple[int, str]] = {
    "rank": (2, "rank(x, w): rolling percentile rank (0..1) of x within the last w bars"),
    "zscore": (2, "zscore(x, w): (x - rolling_mean(x, w)) / rolling_std(x, w)"),
    "delay": (2, "delay(x, n): value of x from n bars ago (n >= 0)"),
    "delta": (2, "delta(x, n): x - delay(x, n) (n >= 1)"),
    "ts_mean": (2, "ts_mean(x, w): rolling mean over the last w bars"),
    "ts_std": (2, "ts_std(x, w): rolling standard deviation over the last w bars"),
    "ts_min": (2, "ts_min(x, w): rolling minimum over the last w bars"),
    "ts_max": (2, "ts_max(x, w): rolling maximum over the last w bars"),
    "corr": (3, "corr(x, y, w): rolling correlation of x and y over the last w bars"),
    "abs": (1, "abs(x): absolute value"),
    "log": (1, "log(x): natural log; non-positive inputs become NaN"),
    "sign": (1, "sign(x): -1, 0 or +1"),
}

_BIN_OPS = {ast.Add: np.add, ast.Sub: np.subtract,
            ast.Mult: np.multiply, ast.Div: np.divide}
_CMP_OPS = {ast.Lt: np.less, ast.LtE: np.less_equal,
            ast.Gt: np.greater, ast.GtE: np.greater_equal,
            ast.Eq: np.equal, ast.NotEq: np.not_equal}
_UNARY_OPS = {ast.USub: np.negative, ast.UAdd: np.positive}

# functions whose 2nd (and delta's) int argument is a *lag* — 0 allowed for
# delay, >= 1 for delta; everything else takes a rolling *window* (>= 1)
_LAG_FUNCS = {"delay", "delta"}


def function_grammar() -> str:
    """Human/LLM-readable description of the expression language (used in
    the mining prompt so proposals and validation share one source)."""
    lines = [f"- {doc}" for _, doc in FUNCTIONS.values()]
    return (
        "Columns: " + ", ".join(BASE_COLUMNS) + "\n"
        "Operators: + - * /, unary -, comparisons (< <= > >= == !=; result is 0/1)\n"
        "Functions (windows and lags must be integer literals):\n"
        + "\n".join(lines)
    )


class FactorExpr:
    """A parsed, validated factor expression. Construct via ``parse``."""

    def __init__(self, text: str, tree: ast.expr, names: frozenset[str]):
        self.text = text
        self._tree = tree
        self.names = names  # column names the expression reads

    def __repr__(self) -> str:  # pragma: no cover - debugging nicety
        return f"FactorExpr({self.text!r})"

    # --- parsing -------------------------------------------------------------
    @classmethod
    def parse(cls, text: str, extra_columns: tuple[str, ...] = ()) -> FactorExpr:
        """Parse ``text`` into a validated expression or raise ValueError
        with a reason suitable for feeding back to the proposer."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty expression")
        if len(text) > _MAX_EXPR_LEN:
            raise ValueError(f"expression longer than {_MAX_EXPR_LEN} chars")
        allowed = set(BASE_COLUMNS) | set(extra_columns)
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"syntax error: {exc.msg}") from exc
        names = _validate(tree.body, allowed)
        return cls(text.strip(), tree.body, frozenset(names))

    # --- evaluation ----------------------------------------------------------
    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """Evaluate over a bar frame (columns open/high/low/close/volume,
        case-insensitive, plus any registered metric columns), oldest bar
        first. Returns a float series aligned to ``df.index``; warm-up bars
        are NaN, and division blow-ups are mapped to NaN, never inf."""
        frame = _normalize_columns(df)
        missing = self.names - set(frame.columns)
        if missing:
            raise ValueError(f"dataframe lacks columns: {sorted(missing)}")
        out = _eval_node(self._tree, frame)
        if np.isscalar(out):
            out = pd.Series(float(out), index=frame.index)
        out = out.astype(float)
        return out.replace([np.inf, -np.inf], np.nan)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {c: str(c).lower() for c in df.columns}
    return df.rename(columns=mapping)


def _validate(node: ast.expr, allowed: set[str]) -> set[str]:
    """Recursive whitelist walk. Returns the column names used."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"only numeric constants allowed, got {node.value!r}")
        return set()
    if isinstance(node, ast.Name):
        name = node.id
        if "__" in name:
            raise ValueError(f"forbidden name {name!r}")
        if name not in allowed:
            raise ValueError(f"unknown column {name!r} (allowed: {sorted(allowed)})")
        return {name}
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARY_OPS:
            raise ValueError(f"operator {type(node.op).__name__} not allowed")
        return _validate(node.operand, allowed)
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BIN_OPS:
            raise ValueError(f"operator {type(node.op).__name__} not allowed")
        return _validate(node.left, allowed) | _validate(node.right, allowed)
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1:
            raise ValueError("chained comparisons not allowed")
        if type(node.ops[0]) not in _CMP_OPS:
            raise ValueError(f"comparison {type(node.ops[0]).__name__} not allowed")
        return _validate(node.left, allowed) | _validate(node.comparators[0], allowed)
    if isinstance(node, ast.Call):
        return _validate_call(node, allowed)
    # Attribute, Subscript, Lambda, comprehensions, f-strings, boolean ops,
    # starred args, ... — everything else is an escape attempt or nonsense.
    raise ValueError(f"disallowed syntax: {type(node).__name__}")


def _validate_call(node: ast.Call, allowed: set[str]) -> set[str]:
    if not isinstance(node.func, ast.Name):
        raise ValueError("only plain whitelisted function names may be called")
    fname = node.func.id
    if fname not in FUNCTIONS:
        raise ValueError(f"function {fname!r} not in whitelist "
                         f"{sorted(FUNCTIONS)}")
    if node.keywords:
        raise ValueError(f"{fname}: keyword arguments not allowed")
    n_args, _ = FUNCTIONS[fname]
    if len(node.args) != n_args:
        raise ValueError(f"{fname} takes {n_args} argument(s), got {len(node.args)}")
    names = _validate(node.args[0], allowed)
    if fname == "corr":
        names |= _validate(node.args[1], allowed)
    if n_args >= 2:
        last = node.args[-1]
        value = _literal_int(last)
        if value is None:
            raise ValueError(f"{fname}: window/lag must be an integer literal")
        if value < 0:
            # the classic look-ahead smuggle: delay(x, -1) is tomorrow's value
            raise ValueError(f"{fname}: negative lag/window {value} would "
                             "look into the future; refused")
        if fname == "delta" and value < 1:
            raise ValueError("delta: lag must be >= 1")
        if fname not in _LAG_FUNCS and value < 1:
            raise ValueError(f"{fname}: window must be >= 1")
        if value > _MAX_WINDOW:
            raise ValueError(f"{fname}: window/lag {value} exceeds {_MAX_WINDOW}")
    return names


def _literal_int(node: ast.expr) -> int | None:
    """Integer literal, allowing a leading unary minus. None if not one."""
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        sign, node = -1, node.operand
    if (isinstance(node, ast.Constant) and isinstance(node.value, int)
            and not isinstance(node.value, bool)):
        return sign * node.value
    return None


def _as_series(value, index) -> pd.Series:
    if isinstance(value, pd.Series):
        return value
    return pd.Series(float(value), index=index)


def _eval_node(node: ast.expr, df: pd.DataFrame):
    idx = df.index
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return df[node.id].astype(float)
    if isinstance(node, ast.UnaryOp):
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand, df))
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, df)
        right = _eval_node(node.right, df)
        with np.errstate(divide="ignore", invalid="ignore"):
            return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.Compare):
        left = _as_series(_eval_node(node.left, df), idx)
        right = _eval_node(node.comparators[0], df)
        return _CMP_OPS[type(node.ops[0])](left, right).astype(float)
    # validated Call
    fname = node.func.id
    x = _as_series(_eval_node(node.args[0], df), idx)
    if fname == "abs":
        return x.abs()
    if fname == "sign":
        return np.sign(x)
    if fname == "log":
        with np.errstate(divide="ignore", invalid="ignore"):
            return pd.Series(np.where(x > 0, np.log(x.where(x > 0)), np.nan), index=idx)
    w = _literal_int(node.args[-1])
    if fname == "delay":
        return x.shift(w)
    if fname == "delta":
        return x - x.shift(w)
    if fname == "rank":
        return x.rolling(w).rank(pct=True)
    if fname == "zscore":
        std = x.rolling(w).std(ddof=0)
        return (x - x.rolling(w).mean()) / std.where(std > 0)
    if fname == "ts_mean":
        return x.rolling(w).mean()
    if fname == "ts_std":
        return x.rolling(w).std(ddof=0)
    if fname == "ts_min":
        return x.rolling(w).min()
    if fname == "ts_max":
        return x.rolling(w).max()
    if fname == "corr":
        y = _as_series(_eval_node(node.args[1], df), idx)
        with np.errstate(divide="ignore", invalid="ignore"):
            return x.rolling(w).corr(y)
    raise AssertionError(f"unreachable: {fname}")  # pragma: no cover


# --- information-coefficient tooling ----------------------------------------

_MIN_IC_OBS = 8  # below this a rank correlation is pure noise


def forward_returns(close: pd.Series, horizon: int) -> pd.Series:
    """Simple return from t to t+horizon, indexed at t (NaN for the tail)."""
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    c = close.astype(float)
    return c.shift(-horizon) / c - 1.0


def factor_ic(factor_series: pd.Series, fwd_returns: pd.Series,
              method: str = "spearman") -> float | None:
    """Information coefficient: correlation between the factor at t and the
    forward return from t. ``method`` is "spearman" (rank IC, default) or
    "pearson". None on degenerate input (too few paired observations or a
    constant series) — never raises on bad data."""
    if method not in ("spearman", "pearson"):
        raise ValueError(f"unknown method {method!r}")
    pair = pd.concat([factor_series.astype(float), fwd_returns.astype(float)],
                     axis=1, keys=["f", "r"]).dropna()
    if len(pair) < _MIN_IC_OBS:
        return None
    if pair["f"].nunique() < 2 or pair["r"].nunique() < 2:
        return None
    ic = pair["f"].corr(pair["r"], method=method)
    return None if ic is None or math.isnan(ic) else float(ic)


def ic_decay(factor: pd.Series, returns: pd.Series,
             horizons: tuple[int, ...] = (1, 2, 5, 10, 20),
             method: str = "spearman") -> dict[int, float | None]:
    """IC of the same factor against forward returns of growing horizons —
    how fast the signal's information dies. ``returns`` are one-bar simple
    returns aligned to the factor's index."""
    growth = (1.0 + returns.astype(float)).cumprod()
    out: dict[int, float | None] = {}
    for h in horizons:
        fwd = growth.shift(-h) / growth - 1.0
        out[int(h)] = factor_ic(factor, fwd, method=method)
    return out


def evaluate_factor_oos(
    expr: FactorExpr | str,
    bars_df: pd.DataFrame,
    horizon: int = 5,
    k: int = 5,
    method: str = "spearman",
) -> dict:
    """Out-of-sample factor evaluation over purged K-fold splits (P2-02).

    The factor series is computed once over the full frame (each value at
    *t* uses only bars <= t — a parse-time guarantee of ``FactorExpr``);
    the IC is then measured independently on each purged test fold, with
    the embargo sized to the label horizon so the folds' forward-return
    windows cannot overlap the purged boundary.

    Returns ``{ic_mean, ic_std, ic_ir, n_folds, fold_ics, decay}`` where the
    statistical fields are None when no fold produced a usable IC.
    """
    if isinstance(expr, str):
        expr = FactorExpr.parse(expr)
    frame = _normalize_columns(bars_df)
    factor = expr.evaluate(frame)
    close = frame["close"].astype(float)
    fwd = forward_returns(close, horizon)
    n = len(frame)
    splits = purged_kfold_splits(n, k, embargo_frac=horizon / n if n else 0.0)
    fold_ics: list[float] = []
    for _train_idx, test_idx in splits:
        ic = factor_ic(factor.iloc[test_idx], fwd.iloc[test_idx], method=method)
        if ic is not None:
            fold_ics.append(ic)
    result: dict = {
        "ic_mean": None, "ic_std": None, "ic_ir": None,
        "n_folds": len(fold_ics), "fold_ics": fold_ics,
        "decay": ic_decay(factor, close.pct_change(), method=method),
    }
    if fold_ics:
        mean = float(np.mean(fold_ics))
        std = float(np.std(fold_ics))
        result["ic_mean"] = mean
        result["ic_std"] = std
        result["ic_ir"] = mean / std if std > 0 else None
    return result


__all__ = [
    "BASE_COLUMNS",
    "FUNCTIONS",
    "FactorExpr",
    "evaluate_factor_oos",
    "factor_ic",
    "forward_returns",
    "function_grammar",
    "ic_decay",
]
