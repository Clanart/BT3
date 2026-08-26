# Q1359: WombatBribeManager.vote - delegatedPool votes enter pool totals but not the global total

## Question
In wombat/WombatBribeManager.sol, vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Starting from a state where the attacker locks vlMGP, votes and casts inside a single transaction, can an unprivileged EOA use `vote(address[] _lps, int256[] _deltas)` to leave `poolInfos[lp].isActive` inconsistent with `userVotedForPoolInVlmgp[user][lp]`, violating the invariant that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: delegatedPool votes enter pool totals but not the global total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries) under the attacker locks vlMGP, votes and casts inside a single transaction, asserting on every row that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes.
