# Q5891: WombatBribeManager.castVotes - last-block vote sniping before a permissionless cast

## Question
In wombat/WombatBribeManager.sol, castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Does `castVotes(bool swapForBnb)` let an unprivileged caller exploit that under the attacker has just cancelled a cooldown so getUserVotable jumped upward, so that `getVoteForLp(lp) from the Wombat voter` diverges from `poolInfos[lp].totalVoteInVlmgp`, the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has just cancelled a cooldown so getUserVotable jumped upward, call `castVotes(bool swapForBnb)`, and assert `getVoteForLp(lp) from the Wombat voter` equals `poolInfos[lp].totalVoteInVlmgp` and that no account can withdraw more than it put in.
