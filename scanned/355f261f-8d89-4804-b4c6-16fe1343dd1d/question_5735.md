# Q5735: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
In wombat/WombatBribeManager.sol, _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Starting from a state where the bribe contract for the pool registers more than one reward token, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `targetVote computed in castVotes` inconsistent with `totalVotes() from veWom.balanceOf(wombatStaking)`, violating the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the bribe contract for the pool registers more than one reward token, snapshot `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)`, run the attacker's `castVotes(bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
