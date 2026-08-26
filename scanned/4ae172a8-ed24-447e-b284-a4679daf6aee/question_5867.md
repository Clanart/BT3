# Q5867: WombatBribeManager.vote - delegatedPool votes enter pool totals but not the global total

## Question
wombat/WombatBribeManager.sol: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Under the attacker has just cancelled a cooldown so getUserVotable jumped upward, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `delegatedPool votes` unreconciled with `totalVlMgpInVote`, violates the invariant that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: delegatedPool votes enter pool totals but not the global total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that the attacker has just cancelled a cooldown so getUserVotable jumped upward, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes.
