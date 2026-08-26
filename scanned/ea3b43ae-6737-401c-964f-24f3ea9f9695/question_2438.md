# Q2438: WombatBribeManager.vote - existing votes are never revalidated when the ceiling falls

## Question
wombat/WombatBribeManager.sol: nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Under the attacker votes in the block immediately before a known keeper cast, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `userVotedForPoolInVlmgp[user][lp]` unreconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`, violates the invariant that votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: existing votes are never revalidated when the ceiling falls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries) under the attacker votes in the block immediately before a known keeper cast, asserting on every row that votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter.
