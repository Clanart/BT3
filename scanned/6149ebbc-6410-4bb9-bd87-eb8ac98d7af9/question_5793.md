# Q5793: WombatBribeManager.vote - existing votes are never revalidated when the ceiling falls

## Question
Consider wombat/WombatBribeManager.sol, where nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Assuming the victim has a large unsettled balance in the pool rewarder, can an unprivileged attacker turn this into a divergence between `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` via `vote(address[] _lps, int256[] _deltas)`, breaking the invariant that votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: existing votes are never revalidated when the ceiling falls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that the victim has a large unsettled balance in the pool rewarder, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter.
