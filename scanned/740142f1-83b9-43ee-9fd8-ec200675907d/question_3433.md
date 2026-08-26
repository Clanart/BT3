# Q3433: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
In wombat/WombatBribeManager.sol, _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Does `castVotes(bool swapForBnb)` let an unprivileged caller exploit that under the pool the attacker voted for has been deactivated so unvote reverts, so that `delegatedPool votes` diverges from `totalVlMgpInVote`, the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `castVotes(bool swapForBnb)`: constrain the setup so that the pool the attacker voted for has been deactivated so unvote reverts, fuzz the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination), and assert after every call that a fee intended to compensate a keeper must not be capturable by an actor who adds no value.
