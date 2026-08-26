# Q5374: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
Consider wombat/WombatBribeManager.sol, where _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Assuming the attacker passes offsetting positive and negative deltas that net to zero, can an unprivileged attacker turn this into a divergence between `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` via `castVotes(bool swapForBnb)`, breaking the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker passes offsetting positive and negative deltas that net to zero, call `castVotes(bool swapForBnb)`, and assert `totalVlMgpInVote` equals `sum of userTotalVotedInVlmgp over all voters` and that no account can withdraw more than it put in.
