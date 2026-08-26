# Q4520: vlMGPBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
Consider rewards/vlMGPBaseRewarder.sol, where _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Assuming the victim has not settled for several epochs and holds a large userRewards balance, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` via `getReward(address _account, address _receiver)`, breaking the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the victim has not settled for several epochs and holds a large userRewards balance, asserting on every row that a rounding convenience must not create a settlement size at which the forfeit rule stops applying.
