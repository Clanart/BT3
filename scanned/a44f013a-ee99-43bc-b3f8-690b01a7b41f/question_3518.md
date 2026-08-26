# Q3518: WombatBribeManager.voteAndCast - vote and cast in one transaction with no time weighting

## Question
In wombat/WombatBribeManager.sol, voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Can an unprivileged attacker reach this through `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` while the pool the attacker voted for has been deactivated so unvote reverts, and drive `poolInfos[lp].isActive` out of agreement with `userVotedForPoolInVlmgp[user][lp]` - breaking the invariant that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` (mechanism: vote and cast in one transaction with no time weighting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the deltas and the atomic vote-then-cast ordering inside one transaction
- Exploit idea: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the deltas and the atomic vote-then-cast ordering inside one transaction) under the pool the attacker voted for has been deactivated so unvote reverts, asserting on every row that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued.
