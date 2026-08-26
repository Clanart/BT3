# Q5262: WombatBribeManager.vote - delegatedPool votes enter pool totals but not the global total

## Question
Consider wombat/WombatBribeManager.sol, where vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Assuming the attacker passes offsetting positive and negative deltas that net to zero, can an unprivileged attacker turn this into a divergence between `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` via `vote(address[] _lps, int256[] _deltas)`, breaking the invariant that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: delegatedPool votes enter pool totals but not the global total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() skips the userTotalVotedInVlmgp and totalVlMgpInVote updates when msg.sender is delegatedPool, yet it still adds the delta into poolInfos[lp].totalVoteInVlmgp, so the per-pool totals and the global denominator are maintained on different bases. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that the attacker passes offsetting positive and negative deltas that net to zero, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that the sum of per-pool vote totals and the global vote denominator must be derived from the same set of votes.
