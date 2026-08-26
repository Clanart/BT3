# Q0605: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
wombat/WombatBribeManager.sol: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. With the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination under attacker control and a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged caller sequence `castVotes(bool swapForBnb)` so that `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` no longer reconcile, violating the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination) under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, asserting on every row that a fee intended to compensate a keeper must not be capturable by an actor who adds no value.
