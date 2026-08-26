# Q3726: WombatBribeManager.claimAllBribes - claimAllBribes reports the pre-claim estimate rather than the amount delivered

## Question
wombat/WombatBribeManager.sol - earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Can an unprivileged attacker controlling _for (any victim) and the block at which every pool rewarder is settled for them, under the pool the attacker voted for has been deactivated so unvote reverts, exploit this through `claimAllBribes(address _for)` to break the reconciliation between `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` and the invariant that a reported settlement amount must be measured from the balance actually delivered, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes reports the pre-claim estimate rather than the amount delivered)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: a reported settlement amount must be measured from the balance actually delivered; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and the block at which every pool rewarder is settled for them) under the pool the attacker voted for has been deactivated so unvote reverts, asserting on every row that a reported settlement amount must be measured from the balance actually delivered.
