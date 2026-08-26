# Q4614: WombatBribeManager.castVotes - getUserVotable ignores balances in cooldown

## Question
In wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Can an unprivileged attacker reach this through `castVotes(bool swapForBnb)` while delegatedPool is unset so the delegate legs are skipped, and drive `delegatedPool votes` out of agreement with `totalVlMgpInVote` - breaking the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up delegatedPool is unset so the delegate legs are skipped, snapshot `delegatedPool votes` and `totalVlMgpInVote`, run the attacker's `castVotes(bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
