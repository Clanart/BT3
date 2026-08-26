# Q2016: mWOMSVBaseRewarder.getReward - queued backlog released at an attacker-chosen stake distribution

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Assuming the computed forfeit lands just below the _amount / 1000 dust threshold, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` via `getReward(address _account, address _receiver)`, breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the computed forfeit lands just below the _amount / 1000 dust threshold, then assert `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` end identical in both runs.
