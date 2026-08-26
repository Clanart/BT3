# Q4662: WombatBribeManager.castVotes - castVotes writes lastCastTime before the external voter call

## Question
In wombat/WombatBribeManager.sol, lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Starting from a state where delegatedPool is unset so the delegate legs are skipped, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `userTotalVotedInVlmgp[msg.sender]` inconsistent with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, violating the invariant that a cadence marker must only advance once the operation it marks has completed and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under delegatedPool is unset so the delegate legs are skipped, then assert `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` end identical in both runs.
