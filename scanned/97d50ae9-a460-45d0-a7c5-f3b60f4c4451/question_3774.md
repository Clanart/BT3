# Q3774: WombatBribeManager.castVotesAndClaimBribes - getUserVotable ignores balances in cooldown

## Question
Note that in wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Can an attacker holding only tokens bought on market reach it via `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` under the pool the attacker voted for has been deactivated so unvote reverts and force `totalVlMgpInVote` apart from `sum of userTotalVotedInVlmgp over all voters`, breaking the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the pool the attacker voted for has been deactivated so unvote reverts, have the attacker run `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`, then assert the victim's claimable value and the `totalVlMgpInVote` versus `sum of userTotalVotedInVlmgp over all voters` relation are unchanged by the attacker's transaction.
