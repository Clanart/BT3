# Q1770: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
Note that in wombat/WombatBribeManager.sol, _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Can an attacker holding only tokens bought on market reach it via `castVotes(bool swapForBnb)` under the attacker locks vlMGP, votes and casts inside a single transaction and force `getVoteForLp(lp) from the Wombat voter` apart from `poolInfos[lp].totalVoteInVlmgp`, breaking the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker locks vlMGP, votes and casts inside a single transaction, then assert `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` end identical in both runs.
