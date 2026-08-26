# Q5785: WombatBribeManager.vote - delegatedPool votes enter pool totals but not the global total

## Question
In wombat/WombatBribeManager.sol, vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Does `vote(address[] _lps, int256[] _deltas)` let an unprivileged caller exploit that under the victim has a large unsettled balance in the pool rewarder, so that `poolInfos[lp].isActive` diverges from `userVotedForPoolInVlmgp[user][lp]`, the invariant that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: delegatedPool votes enter pool totals but not the global total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has a large unsettled balance in the pool rewarder, then assert `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` end identical in both runs.
