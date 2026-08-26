# Q5632: WombatBribeManager.voteAndCast - vote and cast in one transaction with no time weighting

## Question
Consider wombat/WombatBribeManager.sol, where voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Assuming the attacker passes an lp address that was never registered in poolInfos, can an unprivileged attacker turn this into a divergence between `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` via `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`, breaking the invariant that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` (mechanism: vote and cast in one transaction with no time weighting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the deltas and the atomic vote-then-cast ordering inside one transaction
- Exploit idea: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` sequence atomically under the attacker passes an lp address that was never registered in poolInfos, asserting at the end that `totalVlMgpInVote` still equals `sum of userTotalVotedInVlmgp over all voters` and the PoC's balance delta is non-positive.
