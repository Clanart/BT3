# Q0016: WombatBribeManager.vote - vote and cast in one transaction with no time weighting

## Question
wombat/WombatBribeManager.sol: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `userTotalVotedInVlmgp[msg.sender]` unreconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, violates the invariant that bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: vote and cast in one transaction with no time weighting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: voteAndCast() calls vote() and castVotes() back to back, and BribeRewardPool.stakeFor credits the voter immediately while bribes harvested by the cast are queued into rewardPerTokenStored in the same call, so a voter who arrived one instruction earlier takes a full share of a whole epoch of bribes. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: bribe share must be weighted by the time a vote was actually committed, not by the balance at the instant the bribe is queued; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, call `vote(address[] _lps, int256[] _deltas)`, and assert `userTotalVotedInVlmgp[msg.sender]` equals `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` and that no account can withdraw more than it put in.
