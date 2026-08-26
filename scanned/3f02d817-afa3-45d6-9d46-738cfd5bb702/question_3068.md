# Q3068: mWOMSVBaseRewarder.getReward - queued backlog released at an attacker-chosen stake distribution

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under a large MGP distribution has just been queued and no account has settled yet and force `balanceOf(account)` apart from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker for Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a large MGP distribution has just been queued and no account has settled yet, snapshot `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
