# Q3484: WombatBribeManager.castVotes - castVotes writes lastCastTime before the external voter call

## Question
wombat/WombatBribeManager.sol: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. With the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination under attacker control and the pool the attacker voted for has been deactivated so unvote reverts, can an unprivileged caller sequence `castVotes(bool swapForBnb)` so that `delegatedPool votes` and `totalVlMgpInVote` no longer reconcile, violating the invariant that a cadence marker must only advance once the operation it marks has completed and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination) under the pool the attacker voted for has been deactivated so unvote reverts, asserting on every row that a cadence marker must only advance once the operation it marks has completed.
