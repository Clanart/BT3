# Q4476: WombatBribeManager.vote - existing votes are never revalidated when the ceiling falls

## Question
In wombat/WombatBribeManager.sol, nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Does `vote(address[] _lps, int256[] _deltas)` let an unprivileged caller exploit that under delegatedPool is unset so the delegate legs are skipped, so that `poolInfos[lp].isActive` diverges from `userVotedForPoolInVlmgp[user][lp]`, the invariant that votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: existing votes are never revalidated when the ceiling falls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: nothing re-runs the userTotalVotedInVlmgp against getUserVotable check outside vote(), so a lock that shrinks through any other path leaves votes standing above the ceiling until the voter chooses to vote again. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: votes standing above a user's current entitlement must be reducible by the protocol, not only by the voter; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under delegatedPool is unset so the delegate legs are skipped, then assert `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` end identical in both runs.
