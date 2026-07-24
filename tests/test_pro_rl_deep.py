"""Deep-RL policy (track T3): a torch MLP Q-network behind PolicyProtocol.
Skipped cleanly when torch (the [rl-deep] extra) is absent."""

import pytest

pytest.importorskip("torch")

from tests.pro_fakes import make_bars  # noqa: E402
from tradingagents.pro.rl.deep import DQNPolicy, state_vector  # noqa: E402
from tradingagents.pro.rl.env import ACTIONS, build_transitions  # noqa: E402


def _transitions():
    return build_transitions(make_bars(n=170))


def test_dqn_policy_conforms_to_the_protocol_surface():
    trans = _transitions()
    policy = DQNPolicy(epochs=40, seed=0).fit(trans)
    q = policy.q_values(trans[0].state)
    assert set(q) == set(ACTIONS)
    assert all(isinstance(v, float) for v in q.values())
    # visits reports training experience (so advice isn't auto-gated as unseen)
    assert policy.visits(trans[0].state) == len(trans)


def test_dqn_is_deterministic_under_seed():
    trans = _transitions()
    a = DQNPolicy(epochs=40, seed=1).fit(trans).q_values(trans[0].state)
    b = DQNPolicy(epochs=40, seed=1).fit(trans).q_values(trans[0].state)
    assert a == b


def test_dqn_advisor_integration():
    # the RL advisor depends only on the protocol, so a deep policy drops in
    from tradingagents.pro.rl.advisor import RLAdvisor

    bars = make_bars(n=170)
    policy = DQNPolicy(epochs=40, seed=0).fit(build_transitions(bars))
    advisor = RLAdvisor(policy, min_visits=1)
    readings = advisor.advise(bars)
    assert isinstance(readings, dict)


def test_state_vector_shape_matches_encoding():
    trans = _transitions()
    vec = state_vector(trans[0].state)
    # 3 bucket features + regime one-hot; all floats
    assert len(vec) >= 4 and all(isinstance(v, float) for v in vec)


def test_unfitted_policy_raises():
    with pytest.raises(RuntimeError, match="not fitted"):
        DQNPolicy().q_values(_transitions()[0].state)
