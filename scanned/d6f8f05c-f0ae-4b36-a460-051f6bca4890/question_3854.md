# Q3854: WombatBribeManager.vote - duplicate lp entries inside one vote array

## Question
wombat/WombatBribeManager.sol - vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Can an unprivileged attacker controlling every lp address and every signed delta, including duplicates and offsetting positive and negative entries, under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, exploit this through `vote(address[] _lps, int256[] _deltas)` to break the reconciliation between `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` and the invariant that voting on the same pool twice in one call must be equivalent to voting once with the summed delta, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: duplicate lp entries inside one vote array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: voting on the same pool twice in one call must be equivalent to voting once with the summed delta; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, snapshot `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters`, run the attacker's `vote(address[] _lps, int256[] _deltas)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
