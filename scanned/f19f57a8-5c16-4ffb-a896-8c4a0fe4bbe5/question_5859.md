# Q5859: WombatBribeManager.castVotesAndClaimBribes - getUserVotable ignores balances in cooldown

## Question
In wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Starting from a state where the victim has a large unsettled balance in the pool rewarder, can an unprivileged EOA use `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` to leave `userTotalVotedInVlmgp[msg.sender]` inconsistent with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, violating the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` sequence atomically under the victim has a large unsettled balance in the pool rewarder, asserting at the end that `userTotalVotedInVlmgp[msg.sender]` still equals `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` and the PoC's balance delta is non-positive.
