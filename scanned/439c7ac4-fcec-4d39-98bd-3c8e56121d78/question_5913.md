# Q5913: WombatBribeManager.voteAndCast - getUserVotable ignores balances in cooldown

## Question
wombat/WombatBribeManager.sol - getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Can an unprivileged attacker controlling the deltas and the atomic vote-then-cast ordering inside one transaction, under the attacker has just cancelled a cooldown so getUserVotable jumped upward, exploit this through `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` to break the reconciliation between `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` and the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the deltas and the atomic vote-then-cast ordering inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has just cancelled a cooldown so getUserVotable jumped upward, then assert `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` end identical in both runs.
