# Q0295: WombatBribeManager.vote - castVotes writes lastCastTime before the external voter call

## Question
Consider wombat/WombatBribeManager.sol, where lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Assuming a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged attacker turn this into a divergence between `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` via `vote(address[] _lps, int256[] _deltas)`, breaking the invariant that a cadence marker must only advance once the operation it marks has completed and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries) under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, asserting on every row that a cadence marker must only advance once the operation it marks has completed.
