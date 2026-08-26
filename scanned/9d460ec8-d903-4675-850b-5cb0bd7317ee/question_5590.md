# Q5590: WombatBribeManager.castVotes - vote and cast in one transaction with no time weighting

## Question
wombat/WombatBribeManager.sol: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Under the attacker passes an lp address that was never registered in poolInfos, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `poolInfos[lp].totalVoteInVlmgp` unreconciled with `totalVlMgpInVote`, violates the invariant that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: vote and cast in one transaction with no time weighting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `castVotes(bool swapForBnb)` sequence atomically under the attacker passes an lp address that was never registered in poolInfos, asserting at the end that `poolInfos[lp].totalVoteInVlmgp` still equals `totalVlMgpInVote` and the PoC's balance delta is non-positive.
