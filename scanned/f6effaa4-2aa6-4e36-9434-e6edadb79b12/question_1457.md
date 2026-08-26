# Q1457: vlMGPBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
Note that in rewards/vlMGPBaseRewarder.sol, _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under the account's slot matured recently so the percent has only just begun to decay and force `balanceOf(account)` apart from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, breaking the invariant that total forfeit must be invariant to how a user splits their settlements for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the account's slot matured recently so the percent has only just begun to decay, asserting on every row that total forfeit must be invariant to how a user splits their settlements.
