# Q5807: WombatBribeManager.castVotes - vote and cast in one transaction with no time weighting

## Question
In wombat/WombatBribeManager.sol, voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Can an unprivileged attacker reach this through `castVotes(bool swapForBnb)` while the victim has a large unsettled balance in the pool rewarder, and drive `userVotedForPoolInVlmgp[user][lp]` out of agreement with `IBribeRewardPool(pool.rewarder).balanceOf(user)` - breaking the invariant that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: vote and cast in one transaction with no time weighting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination) under the victim has a large unsettled balance in the pool rewarder, asserting on every row that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued.
