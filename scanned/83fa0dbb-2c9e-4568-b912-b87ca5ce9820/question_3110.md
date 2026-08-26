# Q3110: WombatBribeManager.vote - vote and cast in one transaction with no time weighting

## Question
In wombat/WombatBribeManager.sol, voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Does `vote(address[] _lps, int256[] _deltas)` let an unprivileged caller exploit that under the pool the attacker voted for has been deactivated so unvote reverts, so that `userVotedForPoolInVlmgp[user][lp]` diverges from `IBribeRewardPool(pool.rewarder).balanceOf(user)`, the invariant that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: vote and cast in one transaction with no time weighting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under the pool the attacker voted for has been deactivated so unvote reverts, asserting at the end that `userVotedForPoolInVlmgp[user][lp]` still equals `IBribeRewardPool(pool.rewarder).balanceOf(user)` and the PoC's balance delta is non-positive.
