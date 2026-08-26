# Q4929: WombatBribeManager.vote - duplicate lp entries inside one vote array

## Question
wombat/WombatBribeManager.sol: vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Under the attacker passes the same lp address several times in one array, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `targetVote computed in castVotes` unreconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`, violates the invariant that voting on the same pool twice in one call must be equivalent to voting once with the summed delta, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: duplicate lp entries inside one vote array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: voting on the same pool twice in one call must be equivalent to voting once with the summed delta; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries) under the attacker passes the same lp address several times in one array, asserting on every row that voting on the same pool twice in one call must be equivalent to voting once with the summed delta.
