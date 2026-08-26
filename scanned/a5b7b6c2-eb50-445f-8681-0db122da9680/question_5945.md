# Q5945: WombatBribeManager.vote - last-block vote sniping before a permissionless cast

## Question
wombat/WombatBribeManager.sol: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. With every lp address and every signed delta, including duplicates and offsetting positive and negative entries under attacker control and a keeper castVotes transaction is pending in the mempool, can an unprivileged caller sequence `vote(address[] _lps, int256[] _deltas)` so that `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` no longer reconcile, violating the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: a keeper castVotes transaction is pending in the mempool.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a keeper castVotes transaction is pending in the mempool, snapshot `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)`, run the attacker's `vote(address[] _lps, int256[] _deltas)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
