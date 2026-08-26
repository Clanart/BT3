# Q4468: vlMGPBaseRewarder.getRewards - queued backlog released at an attacker-chosen stake distribution

## Question
In rewards/vlMGPBaseRewarder.sol, when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Does `getRewards(address _account, address _receiver, address[] _rewardTokens)` let an unprivileged caller exploit that under the victim has not settled for several epochs and holds a large userRewards balance, so that `_calExpireForfeit(account,_amount)` diverges from `vlMGP.getRewardablePercentWAD(account)`, the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under the victim has not settled for several epochs and holds a large userRewards balance, asserting at the end that `_calExpireForfeit(account,_amount)` still equals `vlMGP.getRewardablePercentWAD(account)` and the PoC's balance delta is non-positive.
