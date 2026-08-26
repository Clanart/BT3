# Q3127: WombatBribeManager.vote - last-block vote sniping before a permissionless cast

## Question
wombat/WombatBribeManager.sol: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Under the pool the attacker voted for has been deactivated so unvote reverts, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `targetVote computed in castVotes` unreconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`, violates the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool the attacker voted for has been deactivated so unvote reverts, call `vote(address[] _lps, int256[] _deltas)`, and assert `targetVote computed in castVotes` equals `totalVotes() from veWom.balanceOf(wombatStaking)` and that no account can withdraw more than it put in.
