# Q3093: WombatBribeManager.castVotesAndClaimBribes - getUserVotable ignores balances in cooldown

## Question
In wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Does `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` let an unprivileged caller exploit that under the attacker votes in the block immediately before a known keeper cast, so that `poolInfos[lp].totalVoteInVlmgp` diverges from `totalVlMgpInVote`, the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`: constrain the setup so that the attacker votes in the block immediately before a known keeper cast, fuzz the attacker inputs (the cast and the immediately following claim inside one transaction), and assert after every call that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance.
