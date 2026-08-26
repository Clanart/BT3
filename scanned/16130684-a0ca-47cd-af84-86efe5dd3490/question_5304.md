# Q5304: WombatBribeManager.vote - castVotes writes lastCastTime before the external voter call

## Question
In wombat/WombatBribeManager.sol, lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Starting from a state where the attacker passes offsetting positive and negative deltas that net to zero, can an unprivileged EOA use `vote(address[] _lps, int256[] _deltas)` to leave `userTotalVotedInVlmgp[msg.sender]` inconsistent with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, violating the invariant that a cadence marker must only advance once the operation it marks has completed and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker passes offsetting positive and negative deltas that net to zero, then assert `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` end identical in both runs.
