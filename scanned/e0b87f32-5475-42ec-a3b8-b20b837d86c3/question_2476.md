# Q2476: mWOMSVBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under the computed forfeit lands just above the _amount / 1000 dust threshold and force `_calExpireForfeit(account,_amount)` apart from `mWOMSV.getRewardablePercentWAD(account)`, breaking the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the computed forfeit lands just above the _amount / 1000 dust threshold, call `getReward(address _account, address _receiver)`, and assert `_calExpireForfeit(account,_amount)` equals `mWOMSV.getRewardablePercentWAD(account)` and that no account can withdraw more than it put in.
