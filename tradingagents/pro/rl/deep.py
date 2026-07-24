"""Deep-RL policy behind the PolicyProtocol seam (track T3, ADR-0025).

``DQNPolicy`` implements the same ``q_values`` / ``visits`` surface as the
tabular ``QTablePolicy``, so ``RLAdvisor`` and the trainer use it unchanged.
Given the offline, FULL-FEEDBACK transitions (every action's reward is known at
each step — see rl/env.py), a deep Q-network reduces to supervised regression:
a small MLP maps the state features to a Q-value per action, fit to the known
per-action rewards (MSE). torch is an opt-in extra ([rl-deep]) behind a guarded
import; seeded + full-batch so a fit is reproducible.

PPO/SAC are intentionally NOT implemented here: they need online rollouts / an
interactive environment, which the offline full-feedback dataset doesn't
provide — DQN-as-regression is the honest fit for this data.
"""

from __future__ import annotations

from collections.abc import Sequence

from tradingagents.contracts import MarketRegime
from tradingagents.pro.rl.env import ACTIONS, Transition
from tradingagents.pro.rl.features import RLState

# stable regime ordering for the one-hot state encoding
_REGIMES: tuple[str, ...] = tuple(r.value for r in MarketRegime)
_STATE_DIM = 3 + len(_REGIMES)  # trend/vol/zscore buckets + regime one-hot


def _require_torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise ImportError(
            "deep RL needs torch — install tradingagents[rl-deep]") from exc
    return torch


def state_vector(state: RLState) -> list[float]:
    """Numeric encoding of a (discrete) RLState for the network: the three
    bucket indices plus a one-hot of the regime."""
    onehot = [1.0 if state.regime == r else 0.0 for r in _REGIMES]
    return [float(state.trend_bucket), float(state.vol_bucket),
            float(state.zscore_bucket), *onehot]


class DQNPolicy:
    """MLP Q-value estimator over the offline full-feedback transitions.
    Implements PolicyProtocol (q_values / visits)."""

    def __init__(self, hidden: int = 16, epochs: int = 300, lr: float = 0.01,
                 seed: int = 0):
        self.hidden = hidden
        self.epochs = epochs
        self.lr = lr
        self.seed = seed
        self._net = None
        self._torch = None
        self._n_train = 0

    def fit(self, transitions: Sequence[Transition]) -> DQNPolicy:
        torch = _require_torch()
        self._torch = torch
        torch.manual_seed(self.seed)
        xs = [state_vector(t.state) for t in transitions]
        ys = [[t.rewards[a] for a in ACTIONS] for t in transitions]
        if len(xs) < 1:
            raise ValueError("need >= 1 transition to fit")
        x = torch.tensor(xs, dtype=torch.float32)
        y = torch.tensor(ys, dtype=torch.float32)
        net = torch.nn.Sequential(
            torch.nn.Linear(_STATE_DIM, self.hidden),
            torch.nn.Tanh(),
            torch.nn.Linear(self.hidden, len(ACTIONS)))
        opt = torch.optim.Adam(net.parameters(), lr=self.lr)
        loss_fn = torch.nn.MSELoss()
        net.train()
        for _ in range(self.epochs):  # full-batch → deterministic under seed
            opt.zero_grad()
            loss = loss_fn(net(x), y)
            loss.backward()
            opt.step()
        net.eval()
        self._net = net
        self._n_train = len(xs)
        return self

    def q_values(self, state: RLState) -> dict[str, float]:
        if self._net is None:
            raise RuntimeError("policy is not fitted")
        torch = self._torch
        with torch.no_grad():
            out = self._net(torch.tensor([state_vector(state)],
                                         dtype=torch.float32))[0]
        return {a: float(out[i]) for i, a in enumerate(ACTIONS)}

    def visits(self, state: RLState) -> int:
        """A function approximator has no per-state visit counts; report the
        training-set size as the policy's overall experience (so its advice
        isn't auto-gated as 'unseen')."""
        return self._n_train


__all__ = ["DQNPolicy", "state_vector"]
