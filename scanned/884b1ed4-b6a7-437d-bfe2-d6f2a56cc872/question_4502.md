# Q4502: WombatBribeManager.vote - castVotes writes lastCastTime before the external voter call

## Question
wombat/WombatBribeManager.sol: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. With every lp address and every signed delta, including duplicates and offsetting positive and negative entries under attacker control and delegatedPool is unset so the delegate legs are skipped, can an unprivileged caller sequence `vote(address[] _lps, int256[] _deltas)` so that `delegatedPool votes` and `totalVlMgpInVote` no longer reconcile, violating the invariant that a cadence marker must only advance once the operation it marks has completed and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under delegatedPool is unset so the delegate legs are skipped, asserting at the end that `delegatedPool votes` still equals `totalVlMgpInVote` and the PoC's balance delta is non-positive.
