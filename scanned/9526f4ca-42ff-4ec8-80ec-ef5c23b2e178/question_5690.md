# Q5690: WombatBribeManager.vote - last-block vote sniping before a permissionless cast

## Question
Consider wombat/WombatBribeManager.sol, where castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Assuming the bribe contract for the pool registers more than one reward token, can an unprivileged attacker turn this into a divergence between `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` via `vote(address[] _lps, int256[] _deltas)`, breaking the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bribe contract for the pool registers more than one reward token, call `vote(address[] _lps, int256[] _deltas)`, and assert `poolInfos[lp].totalVoteInVlmgp` equals `totalVlMgpInVote` and that no account can withdraw more than it put in.
