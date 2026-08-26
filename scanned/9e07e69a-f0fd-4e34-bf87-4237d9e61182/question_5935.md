# Q5935: WombatBribeManager.claimAllBribes - claimAllBribes reports the pre-claim estimate rather than the amount delivered

## Question
In wombat/WombatBribeManager.sol, earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Does `claimAllBribes(address _for)` let an unprivileged caller exploit that under the attacker has just cancelled a cooldown so getUserVotable jumped upward, so that `delegatedPool votes` diverges from `totalVlMgpInVote`, the invariant that a reported settlement amount must be measured from the balance actually delivered is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes reports the pre-claim estimate rather than the amount delivered)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a reported settlement amount must be measured from the balance actually delivered; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `claimAllBribes(address _for)` sequence atomically under the attacker has just cancelled a cooldown so getUserVotable jumped upward, asserting at the end that `delegatedPool votes` still equals `totalVlMgpInVote` and the PoC's balance delta is non-positive.
