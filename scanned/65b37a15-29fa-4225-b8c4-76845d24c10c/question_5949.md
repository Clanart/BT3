# Q5949: WombatBribeManager.vote - delegatedPool votes enter pool totals but not the global total

## Question
wombat/WombatBribeManager.sol: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. With every lp address and every signed delta, including duplicates and offsetting positive and negative entries under attacker control and a keeper castVotes transaction is pending in the mempool, can an unprivileged caller sequence `vote(address[] _lps, int256[] _deltas)` so that `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` no longer reconcile, violating the invariant that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: delegatedPool votes enter pool totals but not the global total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Precondition: a keeper castVotes transaction is pending in the mempool.
- Invariant to test: the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange a keeper castVotes transaction is pending in the mempool, call `vote(address[] _lps, int256[] _deltas)`, and assert `earnedRewards reported by claimAllBribes` equals `the tokens actually transferred by getReward` and that no account can withdraw more than it put in.
