# Q4260: vlMGPBaseRewarder.getReward - queued backlog released at an attacker-chosen stake distribution

## Question
Note that in rewards/vlMGPBaseRewarder.sol, when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under the attacker locks one block before a known large settlement and unlocks one block after and force `_calExpireForfeit(account,_amount)` apart from `vlMGP.getRewardablePercentWAD(account)`, breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker for Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker locks one block before a known large settlement and unlocks one block after, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `_calExpireForfeit(account,_amount)` versus `vlMGP.getRewardablePercentWAD(account)` relation are unchanged by the attacker's transaction.
