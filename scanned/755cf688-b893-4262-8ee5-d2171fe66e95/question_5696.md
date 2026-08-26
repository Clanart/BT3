# Q5696: WombatBribeManager.vote - delegatedPool votes enter pool totals but not the global total

## Question
wombat/WombatBribeManager.sol - vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Can an unprivileged attacker controlling every lp address and every signed delta, including duplicates and offsetting positive and negative entries, under the bribe contract for the pool registers more than one reward token, exploit this through `vote(address[] _lps, int256[] _deltas)` to break the reconciliation between `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` and the invariant that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: delegatedPool votes enter pool totals but not the global total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under the bribe contract for the pool registers more than one reward token, asserting at the end that `getVoteForLp(lp) from the Wombat voter` still equals `poolInfos[lp].totalVoteInVlmgp` and the PoC's balance delta is non-positive.
