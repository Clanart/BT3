# Q4896: WombatBribeManager.vote - last-block vote sniping before a permissionless cast

## Question
In wombat/WombatBribeManager.sol, castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Can an unprivileged attacker reach this through `vote(address[] _lps, int256[] _deltas)` while the attacker passes the same lp address several times in one array, and drive `delegatedPool votes` out of agreement with `totalVlMgpInVote` - breaking the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that the attacker passes the same lp address several times in one array, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed.
