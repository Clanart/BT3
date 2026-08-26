# Q3806: WombatBribeManager.vote - last-block vote sniping before a permissionless cast

## Question
Note that in wombat/WombatBribeManager.sol, castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Can an attacker holding only tokens bought on market reach it via `vote(address[] _lps, int256[] _deltas)` under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp and force `getVoteForLp(lp) from the Wombat voter` apart from `poolInfos[lp].totalVoteInVlmgp`, breaking the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed.
