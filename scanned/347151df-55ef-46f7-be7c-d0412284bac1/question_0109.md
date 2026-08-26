# Q0109: WombatBribeManager.vote - delegatedPool votes enter pool totals but not the global total

## Question
wombat/WombatBribeManager.sol: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. With every lp address and every signed delta, including duplicates and offsetting positive and negative entries under attacker control and a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged caller sequence `vote(address[] _lps, int256[] _deltas)` so that `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` no longer reconcile, violating the invariant that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: delegatedPool votes enter pool totals but not the global total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, call `vote(address[] _lps, int256[] _deltas)`, and assert `getVoteForLp(lp) from the Wombat voter` equals `poolInfos[lp].totalVoteInVlmgp` and that no account can withdraw more than it put in.
