# Q5853: WombatBribeManager.claimAllBribes - claimAllBribes reports the pre-claim estimate rather than the amount delivered

## Question
In wombat/WombatBribeManager.sol, earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Starting from a state where the victim has a large unsettled balance in the pool rewarder, can an unprivileged EOA use `claimAllBribes(address _for)` to leave `poolInfos[lp].isActive` inconsistent with `userVotedForPoolInVlmgp[user][lp]`, violating the invariant that a reported settlement amount must be measured from the balance actually delivered and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes reports the pre-claim estimate rather than the amount delivered)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: a reported settlement amount must be measured from the balance actually delivered; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled balance in the pool rewarder, have the attacker run `claimAllBribes(address _for)`, then assert the victim's claimable value and the `poolInfos[lp].isActive` versus `userVotedForPoolInVlmgp[user][lp]` relation are unchanged by the attacker's transaction.
