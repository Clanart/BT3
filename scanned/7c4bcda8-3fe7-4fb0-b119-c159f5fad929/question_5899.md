# Q5899: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
wombat/WombatBribeManager.sol: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Under the attacker has just cancelled a cooldown so getUserVotable jumped upward, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `poolInfos[lp].isActive` unreconciled with `userVotedForPoolInVlmgp[user][lp]`, violates the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has just cancelled a cooldown so getUserVotable jumped upward, have the attacker run `castVotes(bool swapForBnb)`, then assert the victim's claimable value and the `poolInfos[lp].isActive` versus `userVotedForPoolInVlmgp[user][lp]` relation are unchanged by the attacker's transaction.
