# Q4841: WombatBribeManager.claimAllBribes - claimAllBribes reports the pre-claim estimate rather than the amount delivered

## Question
wombat/WombatBribeManager.sol: earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Under delegatedPool is unset so the delegate legs are skipped, is there an unprivileged sequence of `claimAllBribes(address _for)` that leaves `poolInfos[lp].totalVoteInVlmgp` unreconciled with `totalVlMgpInVote`, violates the invariant that a reported settlement amount must be measured from the balance actually delivered, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes reports the pre-claim estimate rather than the amount delivered)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a reported settlement amount must be measured from the balance actually delivered; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under delegatedPool is unset so the delegate legs are skipped, then assert `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` end identical in both runs.
