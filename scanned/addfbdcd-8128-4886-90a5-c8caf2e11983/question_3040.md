# Q3040: WombatBribeManager.claimAllBribes - claimAllBribes reports the pre-claim estimate rather than the amount delivered

## Question
Note that in wombat/WombatBribeManager.sol, earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Can an attacker holding only tokens bought on market reach it via `claimAllBribes(address _for)` under the attacker votes in the block immediately before a known keeper cast and force `delegatedPool votes` apart from `totalVlMgpInVote`, breaking the invariant that a reported settlement amount must be measured from the balance actually delivered for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes reports the pre-claim estimate rather than the amount delivered)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: earnedRewards[i] is filled from IBribeRewardPool(pool.rewarder).earned(_for, token) before getReward runs, so the figure returned to the caller is an estimate that is never reconciled against the tokens actually received. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a reported settlement amount must be measured from the balance actually delivered; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the attacker votes in the block immediately before a known keeper cast, have the attacker run `claimAllBribes(address _for)`, then assert the victim's claimable value and the `delegatedPool votes` versus `totalVlMgpInVote` relation are unchanged by the attacker's transaction.
