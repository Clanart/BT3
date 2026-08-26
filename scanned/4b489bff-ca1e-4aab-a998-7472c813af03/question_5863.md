# Q5863: WombatBribeManager.vote - last-block vote sniping before a permissionless cast

## Question
wombat/WombatBribeManager.sol: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Under the attacker has just cancelled a cooldown so getUserVotable jumped upward, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `userVotedForPoolInVlmgp[user][lp]` unreconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`, violates the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has just cancelled a cooldown so getUserVotable jumped upward, call `vote(address[] _lps, int256[] _deltas)`, and assert `userVotedForPoolInVlmgp[user][lp]` equals `IBribeRewardPool(pool.rewarder).balanceOf(user)` and that no account can withdraw more than it put in.
