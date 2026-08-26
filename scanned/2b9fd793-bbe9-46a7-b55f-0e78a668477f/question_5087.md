# Q5087: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
In wombat/WombatBribeManager.sol, _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Starting from a state where the attacker passes the same lp address several times in one array, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `poolInfos[lp].totalVoteInVlmgp` inconsistent with `totalVlMgpInVote`, violating the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker passes the same lp address several times in one array, snapshot `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote`, run the attacker's `castVotes(bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
