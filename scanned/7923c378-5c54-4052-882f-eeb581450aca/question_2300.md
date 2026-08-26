# Q2300: WombatBribeManager.vote - last-block vote sniping before a permissionless cast

## Question
wombat/WombatBribeManager.sol - castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Can an unprivileged attacker controlling every lp address and every signed delta, including duplicates and offsetting positive and negative entries, under the attacker votes in the block immediately before a known keeper cast, exploit this through `vote(address[] _lps, int256[] _deltas)` to break the reconciliation between `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` and the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries) under the attacker votes in the block immediately before a known keeper cast, asserting on every row that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed.
