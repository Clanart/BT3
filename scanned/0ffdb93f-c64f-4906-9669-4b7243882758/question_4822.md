# Q4822: vlMGPBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
In rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while a registered reward token has begun reverting on transfer, and drive `_calExpireForfeit(account,_amount)` out of agreement with `vlMGP.getRewardablePercentWAD(account)` - breaking the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a registered reward token has begun reverting on transfer, call `getReward(address _account, address _receiver)`, and assert `_calExpireForfeit(account,_amount)` equals `vlMGP.getRewardablePercentWAD(account)` and that no account can withdraw more than it put in.
