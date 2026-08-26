# Q4135: vlMGPBaseRewarder.getRewards - queued backlog released at an attacker-chosen stake distribution

## Question
In rewards/vlMGPBaseRewarder.sol, when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Can an unprivileged attacker reach this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` while the attacker locks one block before a known large settlement and unlocks one block after, and drive `rewards[_rewardToken].historicalRewards` out of agreement with `IERC20(_rewardToken).balanceOf(address(this))` - breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the attacker locks one block before a known large settlement and unlocks one block after, asserting on every row that a backlog accrued while the pool was empty must not be assignable to a single one-block locker.
