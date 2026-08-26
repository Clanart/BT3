# Q2254: WombatBribeManager.castVotesAndClaimBribes - getUserVotable ignores balances in cooldown

## Question
Note that in wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Can an attacker holding only tokens bought on market reach it via `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` under the attacker locks vlMGP, votes and casts inside a single transaction and force `userTotalVotedInVlmgp[msg.sender]` apart from `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, breaking the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`: constrain the setup so that the attacker locks vlMGP, votes and casts inside a single transaction, fuzz the attacker inputs (the cast and the immediately following claim inside one transaction), and assert after every call that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance.
