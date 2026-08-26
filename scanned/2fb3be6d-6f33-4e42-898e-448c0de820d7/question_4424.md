# Q4424: WombatBribeManager.vote - delegatedPool votes enter pool totals but not the global total

## Question
wombat/WombatBribeManager.sol - vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Can an unprivileged attacker controlling every lp address and every signed delta, including duplicates and offsetting positive and negative entries, under delegatedPool is unset so the delegate legs are skipped, exploit this through `vote(address[] _lps, int256[] _deltas)` to break the reconciliation between `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` and the invariant that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: delegatedPool votes enter pool totals but not the global total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that delegatedPool is unset so the delegate legs are skipped, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes.
