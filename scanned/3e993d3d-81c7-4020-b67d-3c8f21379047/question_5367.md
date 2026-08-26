# Q5367: WombatBribeManager.castVotes - getUserVotable ignores balances in cooldown

## Question
wombat/WombatBribeManager.sol: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. With the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination under attacker control and the attacker passes offsetting positive and negative deltas that net to zero, can an unprivileged caller sequence `castVotes(bool swapForBnb)` so that `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` no longer reconcile, violating the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination) under the attacker passes offsetting positive and negative deltas that net to zero, asserting on every row that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance.
