# Q4626: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
Note that in wombat/WombatBribeManager.sol, _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Can an attacker holding only tokens bought on market reach it via `castVotes(bool swapForBnb)` under delegatedPool is unset so the delegate legs are skipped and force `userTotalVotedInVlmgp[msg.sender]` apart from `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, breaking the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up delegatedPool is unset so the delegate legs are skipped, snapshot `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, run the attacker's `castVotes(bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
