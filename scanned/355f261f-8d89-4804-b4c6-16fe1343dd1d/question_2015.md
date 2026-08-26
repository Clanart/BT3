# Q2015: vlMGPBaseRewarder.getReward - queued backlog released at an attacker-chosen stake distribution

## Question
rewards/vlMGPBaseRewarder.sol: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and the computed forfeit lands just below the _amount / 1000 dust threshold, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` no longer reconcile, violating the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that the computed forfeit lands just below the _amount / 1000 dust threshold, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that a backlog accrued while the pool was empty must not be assignable to a single one-block locker.
