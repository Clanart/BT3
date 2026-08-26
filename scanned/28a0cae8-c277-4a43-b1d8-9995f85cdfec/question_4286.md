# Q4286: vlMGPBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
In rewards/vlMGPBaseRewarder.sol, _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Starting from a state where the attacker locks one block before a known large settlement and unlocks one block after, can an unprivileged EOA use `getReward(address _account, address _receiver)` to leave `balanceOf(account)` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, violating the invariant that total forfeit must be invariant to how a user splits their settlements and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the attacker locks one block before a known large settlement and unlocks one block after, asserting on every row that total forfeit must be invariant to how a user splits their settlements.
