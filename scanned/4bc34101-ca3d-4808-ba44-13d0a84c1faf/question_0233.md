# Q0233: WombatBribeManager.vote - existing votes are never revalidated when the ceiling falls

## Question
Consider wombat/WombatBribeManager.sol, where nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Assuming a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged attacker turn this into a divergence between `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` via `vote(address[] _lps, int256[] _deltas)`, breaking the invariant that votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: existing votes are never revalidated when the ceiling falls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, asserting at the end that `poolInfos[lp].totalVoteInVlmgp` still equals `totalVlMgpInVote` and the PoC's balance delta is non-positive.
