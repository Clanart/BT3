# Q4962: WombatBribeManager.vote - existing votes are never revalidated when the ceiling falls

## Question
In wombat/WombatBribeManager.sol, nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Starting from a state where the attacker passes the same lp address several times in one array, can an unprivileged EOA use `vote(address[] _lps, int256[] _deltas)` to leave `delegatedPool votes` inconsistent with `totalVlMgpInVote`, violating the invariant that votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: existing votes are never revalidated when the ceiling falls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the attacker passes the same lp address several times in one array, snapshot `delegatedPool votes` and `totalVlMgpInVote`, run the attacker's `vote(address[] _lps, int256[] _deltas)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
