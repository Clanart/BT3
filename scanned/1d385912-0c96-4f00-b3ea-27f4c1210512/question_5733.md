# Q5733: WombatBribeManager.castVotes - getUserVotable ignores balances in cooldown

## Question
wombat/WombatBribeManager.sol: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Under the bribe contract for the pool registers more than one reward token, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `totalVlMgpInVote` unreconciled with `sum of userTotalVotedInVlmgp over all voters`, violates the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe contract for the pool registers more than one reward token, then assert `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` end identical in both runs.
