# Q5781: WombatBribeManager.vote - last-block vote sniping before a permissionless cast

## Question
In wombat/WombatBribeManager.sol, castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Does `vote(address[] _lps, int256[] _deltas)` let an unprivileged caller exploit that under the victim has a large unsettled balance in the pool rewarder, so that `totalVlMgpInVote` diverges from `sum of userTotalVotedInVlmgp over all voters`, the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that the victim has a large unsettled balance in the pool rewarder, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed.
