# Q4398: WombatBribeManager.vote - last-block vote sniping before a permissionless cast

## Question
Note that in wombat/WombatBribeManager.sol, castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Can an attacker holding only tokens bought on market reach it via `vote(address[] _lps, int256[] _deltas)` under delegatedPool is unset so the delegate legs are skipped and force `poolInfos[lp].isActive` apart from `userVotedForPoolInVlmgp[user][lp]`, breaking the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up delegatedPool is unset so the delegate legs are skipped, snapshot `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]`, run the attacker's `vote(address[] _lps, int256[] _deltas)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
