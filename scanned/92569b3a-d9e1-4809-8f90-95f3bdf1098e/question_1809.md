# Q1809: mWOMSVBaseRewarder.getRewards - queued backlog released at an attacker-chosen stake distribution

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Assuming the computed forfeit lands just below the _amount / 1000 dust threshold, can an unprivileged attacker turn this into a divergence between `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the computed forfeit lands just below the _amount / 1000 dust threshold, asserting on every row that a backlog accrued while the pool was empty must not be assignable to a single one-block locker.
