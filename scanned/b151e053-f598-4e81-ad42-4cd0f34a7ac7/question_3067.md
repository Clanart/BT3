# Q3067: vlMGPBaseRewarder.getReward - queued backlog released at an attacker-chosen stake distribution

## Question
In rewards/vlMGPBaseRewarder.sol, when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under a large MGP distribution has just been queued and no account has settled yet, so that `balanceOf(account)` diverges from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under a large MGP distribution has just been queued and no account has settled yet, asserting on every row that a backlog accrued while the pool was empty must not be assignable to a single one-block locker.
