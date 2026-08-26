# Q2903: mWOMSVBaseRewarder.getRewards - queued backlog released at an attacker-chosen stake distribution

## Question
In rewards/mWOMSVBaseRewarder.sol, when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Can an unprivileged attacker reach this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` while a large MGP distribution has just been queued and no account has settled yet, and drive `totalStaked()` out of agreement with `IERC20(mWOMSV).totalSupply()` - breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large MGP distribution has just been queued and no account has settled yet, then assert `totalStaked()` and `IERC20(mWOMSV).totalSupply()` end identical in both runs.
