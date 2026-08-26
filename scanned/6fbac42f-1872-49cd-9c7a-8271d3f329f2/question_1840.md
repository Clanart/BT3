# Q1840: WombatBribeManager.castVotes - castVotes writes lastCastTime before the external voter call

## Question
In wombat/WombatBribeManager.sol, lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Does `castVotes(bool swapForBnb)` let an unprivileged caller exploit that under the attacker locks vlMGP, votes and casts inside a single transaction, so that `getVoteForLp(lp) from the Wombat voter` diverges from `poolInfos[lp].totalVoteInVlmgp`, the invariant that a cadence marker must only advance once the operation it marks has completed is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the attacker locks vlMGP, votes and casts inside a single transaction, have the attacker run `castVotes(bool swapForBnb)`, then assert the victim's claimable value and the `getVoteForLp(lp) from the Wombat voter` versus `poolInfos[lp].totalVoteInVlmgp` relation are unchanged by the attacker's transaction.
