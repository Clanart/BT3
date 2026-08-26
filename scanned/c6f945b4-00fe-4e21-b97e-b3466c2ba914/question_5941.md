# Q5941: WombatBribeManager.castVotesAndClaimBribes - getUserVotable ignores balances in cooldown

## Question
In wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Does `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` let an unprivileged caller exploit that under the attacker has just cancelled a cooldown so getUserVotable jumped upward, so that `poolInfos[lp].totalVoteInVlmgp` diverges from `totalVlMgpInVote`, the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has just cancelled a cooldown so getUserVotable jumped upward, then assert `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` end identical in both runs.
