# Q5727: WombatBribeManager.castVotes - last-block vote sniping before a permissionless cast

## Question
wombat/WombatBribeManager.sol: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. With the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination under attacker control and the bribe contract for the pool registers more than one reward token, can an unprivileged caller sequence `castVotes(bool swapForBnb)` so that `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` no longer reconcile, violating the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe contract for the pool registers more than one reward token, then assert `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` end identical in both runs.
