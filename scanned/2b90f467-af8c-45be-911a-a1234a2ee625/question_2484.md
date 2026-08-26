# Q2484: WombatBribeManager.vote - castVotes writes lastCastTime before the external voter call

## Question
wombat/WombatBribeManager.sol: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Under the attacker votes in the block immediately before a known keeper cast, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `targetVote computed in castVotes` unreconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`, violates the invariant that a cadence marker must only advance once the operation it marks has completed, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries) under the attacker votes in the block immediately before a known keeper cast, asserting on every row that a cadence marker must only advance once the operation it marks has completed.
