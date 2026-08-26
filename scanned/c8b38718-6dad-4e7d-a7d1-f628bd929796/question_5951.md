# Q5951: WombatBribeManager.vote - duplicate lp entries inside one vote array

## Question
In wombat/WombatBribeManager.sol, vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Does `vote(address[] _lps, int256[] _deltas)` let an unprivileged caller exploit that under a keeper castVotes transaction is pending in the mempool, so that `poolInfos[lp].totalVoteInVlmgp` diverges from `totalVlMgpInVote`, the invariant that voting on the same pool twice in one call must be equivalent to voting once with the summed delta is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: duplicate lp entries inside one vote array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Precondition: a keeper castVotes transaction is pending in the mempool.
- Invariant to test: voting on the same pool twice in one call must be equivalent to voting once with the summed delta; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish a keeper castVotes transaction is pending in the mempool, have the attacker run `vote(address[] _lps, int256[] _deltas)`, then assert the victim's claimable value and the `poolInfos[lp].totalVoteInVlmgp` versus `totalVlMgpInVote` relation are unchanged by the attacker's transaction.
