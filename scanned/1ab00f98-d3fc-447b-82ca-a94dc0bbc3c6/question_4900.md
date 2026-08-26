# Q4900: mWOMSVBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
rewards/mWOMSVBaseRewarder.sol: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and a registered reward token has begun reverting on transfer, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` no longer reconcile, violating the invariant that total forfeit must be invariant to how a user splits their settlements and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under a registered reward token has begun reverting on transfer, asserting on every row that total forfeit must be invariant to how a user splits their settlements.
