# Q1360: DelegateVoteRewardPool.harvestAll - queues rewards it may never have received

## Question
rewards/DelegateVoteRewardPool.sol: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Under protocolFee is non-zero and feeCollector is set, is there an unprivileged sequence of `harvestAll()` that leaves `_balances[account]` unreconciled with `totalSupply`, violates the invariant that the reward index may only be raised against tokens the contract has actually received, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queues rewards it may never have received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() feeds earnedRewards straight into _queueNewRewardsWithoutTransfer(), and those figures come from WombatBribeManager.claimAllBribes which reports a pre-claim estimate, so rewardPerTokenStored can be raised for tokens the pool does not hold. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: the reward index may only be raised against tokens the contract has actually received; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish protocolFee is non-zero and feeCollector is set, have the attacker run `harvestAll()`, then assert the victim's claimable value and the `_balances[account]` versus `totalSupply` relation are unchanged by the attacker's transaction.
