# Q5909: WombatBribeManager.voteAndCast - vote and cast in one transaction with no time weighting

## Question
wombat/WombatBribeManager.sol: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Under the attacker has just cancelled a cooldown so getUserVotable jumped upward, is there an unprivileged sequence of `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` that leaves `getVoteForLp(lp) from the Wombat voter` unreconciled with `poolInfos[lp].totalVoteInVlmgp`, violates the invariant that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` (mechanism: vote and cast in one transaction with no time weighting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the deltas and the atomic vote-then-cast ordering inside one transaction
- Exploit idea: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` sequence atomically under the attacker has just cancelled a cooldown so getUserVotable jumped upward, asserting at the end that `getVoteForLp(lp) from the Wombat voter` still equals `poolInfos[lp].totalVoteInVlmgp` and the PoC's balance delta is non-positive.
