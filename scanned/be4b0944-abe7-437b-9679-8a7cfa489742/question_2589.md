# Q2589: vlMGPBaseRewarder.getReward - queued backlog released at an attacker-chosen stake distribution

## Question
rewards/vlMGPBaseRewarder.sol: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Under the computed forfeit lands just above the _amount / 1000 dust threshold, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `totalStaked()` unreconciled with `IERC20(vlMGP).totalSupply()`, violates the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the computed forfeit lands just above the _amount / 1000 dust threshold, then assert `totalStaked()` and `IERC20(vlMGP).totalSupply()` end identical in both runs.
