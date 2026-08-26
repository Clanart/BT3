# Q0450: WombatBribeManager.castVotes - vote and cast in one transaction with no time weighting

## Question
In wombat/WombatBribeManager.sol, voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Starting from a state where a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `totalVlMgpInVote` inconsistent with `sum of userTotalVotedInVlmgp over all voters`, violating the invariant that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: vote and cast in one transaction with no time weighting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, call `castVotes(bool swapForBnb)`, and assert `totalVlMgpInVote` equals `sum of userTotalVotedInVlmgp over all voters` and that no account can withdraw more than it put in.
