# Q2698: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
Note that in wombat/WombatBribeManager.sol, _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Can an attacker holding only tokens bought on market reach it via `castVotes(bool swapForBnb)` under the attacker votes in the block immediately before a known keeper cast and force `poolInfos[lp].isActive` apart from `userVotedForPoolInVlmgp[user][lp]`, breaking the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker votes in the block immediately before a known keeper cast, call `castVotes(bool swapForBnb)`, and assert `poolInfos[lp].isActive` equals `userVotedForPoolInVlmgp[user][lp]` and that no account can withdraw more than it put in.
