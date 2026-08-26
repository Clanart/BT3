# Q5889: WombatBribeManager.castVotes - vote and cast in one transaction with no time weighting

## Question
Consider wombat/WombatBribeManager.sol, where voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Assuming the attacker has just cancelled a cooldown so getUserVotable jumped upward, can an unprivileged attacker turn this into a divergence between `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` via `castVotes(bool swapForBnb)`, breaking the invariant that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: vote and cast in one transaction with no time weighting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has just cancelled a cooldown so getUserVotable jumped upward, have the attacker run `castVotes(bool swapForBnb)`, then assert the victim's claimable value and the `targetVote computed in castVotes` versus `totalVotes() from veWom.balanceOf(wombatStaking)` relation are unchanged by the attacker's transaction.
