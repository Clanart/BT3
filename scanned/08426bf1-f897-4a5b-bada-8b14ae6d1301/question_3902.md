# Q3902: WombatBribeManager.vote - existing votes are never revalidated when the ceiling falls

## Question
wombat/WombatBribeManager.sol: nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `getVoteForLp(lp) from the Wombat voter` unreconciled with `poolInfos[lp].totalVoteInVlmgp`, violates the invariant that votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: existing votes are never revalidated when the ceiling falls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter.
