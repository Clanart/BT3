# Q3895: mWOMSVBaseRewarder.getReward - queued backlog released at an attacker-chosen stake distribution

## Question
rewards/mWOMSVBaseRewarder.sol: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and totalStaked is zero and queuedRewards holds a backlog, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` no longer reconcile, violating the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under totalStaked is zero and queuedRewards holds a backlog, then assert `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` end identical in both runs.
