# Q5539: WombatBribeManager.vote - duplicate lp entries inside one vote array

## Question
wombat/WombatBribeManager.sol: vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. With every lp address and every signed delta, including duplicates and offsetting positive and negative entries under attacker control and the attacker passes an lp address that was never registered in poolInfos, can an unprivileged caller sequence `vote(address[] _lps, int256[] _deltas)` so that `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` no longer reconcile, violating the invariant that voting on the same pool twice in one call must be equivalent to voting once with the summed delta and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: duplicate lp entries inside one vote array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: voting on the same pool twice in one call must be equivalent to voting once with the summed delta; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that the attacker passes an lp address that was never registered in poolInfos, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that voting on the same pool twice in one call must be equivalent to voting once with the summed delta.
