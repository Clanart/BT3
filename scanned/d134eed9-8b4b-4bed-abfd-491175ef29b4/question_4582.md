# Q4582: vlMGPBaseRewarder.getReward - queued backlog released at an attacker-chosen stake distribution

## Question
In rewards/vlMGPBaseRewarder.sol, when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while the victim has not settled for several epochs and holds a large userRewards balance, and drive `userRewards[_rewardToken][account]` out of agreement with `rewards[_rewardToken].rewardPerTokenStored` - breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the victim has not settled for several epochs and holds a large userRewards balance, asserting on every row that a backlog accrued while the pool was empty must not be assignable to a single one-block locker.
