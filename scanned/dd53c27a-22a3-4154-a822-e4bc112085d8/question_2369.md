# Q2369: WombatBribeManager.vote - duplicate lp entries inside one vote array

## Question
wombat/WombatBribeManager.sol: vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Under the attacker votes in the block immediately before a known keeper cast, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `userTotalVotedInVlmgp[msg.sender]` unreconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, violates the invariant that voting on the same pool twice in one call must be equivalent to voting once with the summed delta, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: duplicate lp entries inside one vote array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() iterates the caller's array with no uniqueness check, so the same pool can appear several times and pool.totalVoteInVlmgp, userVotedForPoolInVlmgp and the rewarder stakeFor all mutate repeatedly against a ceiling that is only tested at the end. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: voting on the same pool twice in one call must be equivalent to voting once with the summed delta; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under the attacker votes in the block immediately before a known keeper cast, asserting at the end that `userTotalVotedInVlmgp[msg.sender]` still equals `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` and the PoC's balance delta is non-positive.
