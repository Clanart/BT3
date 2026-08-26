# Q4878: mWOMSVBaseRewarder.getReward - queued backlog released at an attacker-chosen stake distribution

## Question
In rewards/mWOMSVBaseRewarder.sol, when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while a registered reward token has begun reverting on transfer, and drive `totalStaked()` out of agreement with `IERC20(mWOMSV).totalSupply()` - breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that a registered reward token has begun reverting on transfer, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that a backlog accrued while the pool was empty must not be assignable to a single one-block locker.
