# Q3365: WombatBribeManager.castVotes - last-block vote sniping before a permissionless cast

## Question
wombat/WombatBribeManager.sol: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Under the pool the attacker voted for has been deactivated so unvote reverts, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `poolInfos[lp].isActive` unreconciled with `userVotedForPoolInVlmgp[user][lp]`, violates the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool the attacker voted for has been deactivated so unvote reverts, call `castVotes(bool swapForBnb)`, and assert `poolInfos[lp].isActive` equals `userVotedForPoolInVlmgp[user][lp]` and that no account can withdraw more than it put in.
