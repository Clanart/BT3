# Q4874: WombatBribeManager.castVotesAndClaimBribes - getUserVotable ignores balances in cooldown

## Question
Consider wombat/WombatBribeManager.sol, where getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Assuming delegatedPool is unset so the delegate legs are skipped, can an unprivileged attacker turn this into a divergence between `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` via `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`, breaking the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange delegatedPool is unset so the delegate legs are skipped, call `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`, and assert `targetVote computed in castVotes` equals `totalVotes() from veWom.balanceOf(wombatStaking)` and that no account can withdraw more than it put in.
