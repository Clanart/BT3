# Q1267: vlMGPBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
In rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under the account's slot matured recently so the percent has only just begun to decay, so that `forfeitAmount` diverges from `rewardInfo.rewardPerTokenStored`, the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the account's slot matured recently so the percent has only just begun to decay, call `getReward(address _account, address _receiver)`, and assert `forfeitAmount` equals `rewardInfo.rewardPerTokenStored` and that no account can withdraw more than it put in.
