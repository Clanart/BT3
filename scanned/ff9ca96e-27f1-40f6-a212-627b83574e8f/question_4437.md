# Q4437: WombatBribeManager.vote - duplicate lp entries inside one vote array

## Question
In wombat/WombatBribeManager.sol, vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Starting from a state where delegatedPool is unset so the delegate legs are skipped, can an unprivileged EOA use `vote(address[] _lps, int256[] _deltas)` to leave `userVotedForPoolInVlmgp[user][lp]` inconsistent with `IBribeRewardPool(pool.rewarder).balanceOf(user)`, violating the invariant that voting on the same pool twice in one call must be equivalent to voting once with the summed delta and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: duplicate lp entries inside one vote array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: voting on the same pool twice in one call must be equivalent to voting once with the summed delta; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under delegatedPool is unset so the delegate legs are skipped, then assert `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` end identical in both runs.
