# Q4521: mWOMSVBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
In rewards/mWOMSVBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Starting from a state where the victim has not settled for several epochs and holds a large userRewards balance, can an unprivileged EOA use `getReward(address _account, address _receiver)` to leave `rewards[_rewardToken].historicalRewards` inconsistent with `IERC20(_rewardToken).balanceOf(address(this))`, violating the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the victim has not settled for several epochs and holds a large userRewards balance, snapshot `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
