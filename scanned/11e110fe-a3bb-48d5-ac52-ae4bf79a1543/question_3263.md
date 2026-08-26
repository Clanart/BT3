# Q3263: WombatBribeManager.vote - castVotes writes lastCastTime before the external voter call

## Question
Consider wombat/WombatBribeManager.sol, where lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Assuming the pool the attacker voted for has been deactivated so unvote reverts, can an unprivileged attacker turn this into a divergence between `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` via `vote(address[] _lps, int256[] _deltas)`, breaking the invariant that a cadence marker must only advance once the operation it marks has completed and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that the pool the attacker voted for has been deactivated so unvote reverts, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that a cadence marker must only advance once the operation it marks has completed.
