# Q3926: mWOMSVBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
In rewards/mWOMSVBaseRewarder.sol, _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Starting from a state where totalStaked is zero and queuedRewards holds a backlog, can an unprivileged EOA use `getReward(address _account, address _receiver)` to leave `totalStaked()` inconsistent with `IERC20(mWOMSV).totalSupply()`, violating the invariant that total forfeit must be invariant to how a user splits their settlements and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that totalStaked is zero and queuedRewards holds a backlog, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that total forfeit must be invariant to how a user splits their settlements.
