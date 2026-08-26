# Q5608: WombatBribeManager.castVotes - getUserVotable ignores balances in cooldown

## Question
Consider wombat/WombatBribeManager.sol, where getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Assuming the attacker passes an lp address that was never registered in poolInfos, can an unprivileged attacker turn this into a divergence between `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` via `castVotes(bool swapForBnb)`, breaking the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker passes an lp address that was never registered in poolInfos, then assert `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` end identical in both runs.
