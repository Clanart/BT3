# Q3535: BribeRewardPool.withdrawFor - queued backlog while totalSupply is zero

## Question
In rewards/BribeRewardPool.sol, _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Does `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` let an unprivileged caller exploit that under the attacker calls the inherited donateRewards for the registered bribe token, so that `rewards[_rewardToken].queuedRewards` diverges from `totalSupply at the moment of the flush`, the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker calls the inherited donateRewards for the registered bribe token, have the attacker run `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, then assert the victim's claimable value and the `rewards[_rewardToken].queuedRewards` versus `totalSupply at the moment of the flush` relation are unchanged by the attacker's transaction.
