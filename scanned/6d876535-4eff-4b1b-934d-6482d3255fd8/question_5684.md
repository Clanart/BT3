# Q5684: WombatBribeManager.castVotesAndClaimBribes - getUserVotable ignores balances in cooldown

## Question
wombat/WombatBribeManager.sol - getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Can an unprivileged attacker controlling the cast and the immediately following claim inside one transaction, under the attacker passes an lp address that was never registered in poolInfos, exploit this through `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` to break the reconciliation between `delegatedPool votes` and `totalVlMgpInVote` and the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`: constrain the setup so that the attacker passes an lp address that was never registered in poolInfos, fuzz the attacker inputs (the cast and the immediately following claim inside one transaction), and assert after every call that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance.
