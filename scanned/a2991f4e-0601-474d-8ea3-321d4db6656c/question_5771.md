# Q5771: WombatBribeManager.claimAllBribes - claimAllBribes reports the pre-claim estimate rather than the amount delivered

## Question
wombat/WombatBribeManager.sol: earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. With _for (any victim) and the block at which every pool rewarder is settled for them under attacker control and the bribe contract for the pool registers more than one reward token, can an unprivileged caller sequence `claimAllBribes(address _for)` so that `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` no longer reconcile, violating the invariant that a reported settlement amount must be measured from the balance actually delivered and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes reports the pre-claim estimate rather than the amount delivered)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: a reported settlement amount must be measured from the balance actually delivered; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and the block at which every pool rewarder is settled for them) under the bribe contract for the pool registers more than one reward token, asserting on every row that a reported settlement amount must be measured from the balance actually delivered.
