# Q3416: WombatBribeManager.castVotes - getUserVotable ignores balances in cooldown

## Question
Consider wombat/WombatBribeManager.sol, where getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Assuming the pool the attacker voted for has been deactivated so unvote reverts, can an unprivileged attacker turn this into a divergence between `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` via `castVotes(bool swapForBnb)`, breaking the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `castVotes(bool swapForBnb)` sequence atomically under the pool the attacker voted for has been deactivated so unvote reverts, asserting at the end that `getVoteForLp(lp) from the Wombat voter` still equals `poolInfos[lp].totalVoteInVlmgp` and the PoC's balance delta is non-positive.
