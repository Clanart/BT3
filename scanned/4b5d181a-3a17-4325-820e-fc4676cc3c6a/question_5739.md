# Q5739: WombatBribeManager.castVotes - lastCastTime is written but never enforced

## Question
wombat/WombatBribeManager.sol: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Under the bribe contract for the pool registers more than one reward token, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `userVotedForPoolInVlmgp[user][lp]` unreconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`, violates the invariant that a recorded cadence variable must actually gate the operation it appears to pace, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: lastCastTime is written but never enforced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: a recorded cadence variable must actually gate the operation it appears to pace; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the bribe contract for the pool registers more than one reward token, have the attacker run `castVotes(bool swapForBnb)`, then assert the victim's claimable value and the `userVotedForPoolInVlmgp[user][lp]` versus `IBribeRewardPool(pool.rewarder).balanceOf(user)` relation are unchanged by the attacker's transaction.
