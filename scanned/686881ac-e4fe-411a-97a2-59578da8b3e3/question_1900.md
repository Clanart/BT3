# Q1900: vlMGPBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
Note that in rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under the computed forfeit lands just below the _amount / 1000 dust threshold and force `rewards[_rewardToken].historicalRewards` apart from `IERC20(_rewardToken).balanceOf(address(this))`, breaking the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the computed forfeit lands just below the _amount / 1000 dust threshold, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `rewards[_rewardToken].historicalRewards` versus `IERC20(_rewardToken).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
