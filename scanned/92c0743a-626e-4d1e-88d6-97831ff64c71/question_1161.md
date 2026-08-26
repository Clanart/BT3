# Q1161: WombatBribeManager.claimAllBribes - claimAllBribes reports the pre-claim estimate rather than the amount delivered

## Question
wombat/WombatBribeManager.sol: earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, is there an unprivileged sequence of `claimAllBribes(address _for)` that leaves `getVoteForLp(lp) from the Wombat voter` unreconciled with `poolInfos[lp].totalVoteInVlmgp`, violates the invariant that a reported settlement amount must be measured from the balance actually delivered, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes reports the pre-claim estimate rather than the amount delivered)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a reported settlement amount must be measured from the balance actually delivered; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, snapshot `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp`, run the attacker's `claimAllBribes(address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
