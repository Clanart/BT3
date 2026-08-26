# Q1332: WombatBribeManager.vote - the vote ceiling is only checked on the net total

## Question
wombat/WombatBribeManager.sol: vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Under the attacker locks vlMGP, votes and casts inside a single transaction, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `getVoteForLp(lp) from the Wombat voter` unreconciled with `poolInfos[lp].totalVoteInVlmgp`, violates the invariant that the vote ceiling must hold at every point of the update, not only on the net result, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: the vote ceiling is only checked on the net total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: the vote ceiling must hold at every point of the update, not only on the net result; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries) under the attacker locks vlMGP, votes and casts inside a single transaction, asserting on every row that the vote ceiling must hold at every point of the update, not only on the net result.
