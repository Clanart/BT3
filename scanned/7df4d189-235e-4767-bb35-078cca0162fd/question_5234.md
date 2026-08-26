# Q5234: WombatBribeManager.castVotesAndClaimBribes - getUserVotable ignores balances in cooldown

## Question
Consider wombat/WombatBribeManager.sol, where getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Assuming the attacker passes the same lp address several times in one array, can an unprivileged attacker turn this into a divergence between `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` via `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`, breaking the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the attacker passes the same lp address several times in one array, have the attacker run `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`, then assert the victim's claimable value and the `getVoteForLp(lp) from the Wombat voter` versus `poolInfos[lp].totalVoteInVlmgp` relation are unchanged by the attacker's transaction.
