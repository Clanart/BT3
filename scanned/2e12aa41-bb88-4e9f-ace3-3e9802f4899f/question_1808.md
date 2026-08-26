# Q1808: vlMGPBaseRewarder.getRewards - queued backlog released at an attacker-chosen stake distribution

## Question
rewards/vlMGPBaseRewarder.sol: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. With the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor under attacker control and the computed forfeit lands just below the _amount / 1000 dust threshold, can an unprivileged caller sequence `getRewards(address _account, address _receiver, address[] _rewardTokens)` so that `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` no longer reconcile, violating the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the computed forfeit lands just below the _amount / 1000 dust threshold, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `_calExpireForfeit(account,_amount)` versus `vlMGP.getRewardablePercentWAD(account)` relation are unchanged by the attacker's transaction.
