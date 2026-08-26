# Q0481: WombatBribeManager.castVotes - last-block vote sniping before a permissionless cast

## Question
In wombat/WombatBribeManager.sol, castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Starting from a state where a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `userVotedForPoolInVlmgp[user][lp]` inconsistent with `IBribeRewardPool(pool.rewarder).balanceOf(user)`, violating the invariant that an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: last-block vote sniping before a permissionless cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() is callable by anyone at any time and there is no minimum holding period between vote() and the cast, so an attacker who votes in the block immediately before a cast dilutes every voter who held the position for the whole epoch. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: an epoch of accrued bribes must not be capturable by a position opened moments before it is distributed; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, have the attacker run `castVotes(bool swapForBnb)`, then assert the victim's claimable value and the `userVotedForPoolInVlmgp[user][lp]` versus `IBribeRewardPool(pool.rewarder).balanceOf(user)` relation are unchanged by the attacker's transaction.
