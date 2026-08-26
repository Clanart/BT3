# Q4607: mWOMSVBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
rewards/mWOMSVBaseRewarder.sol - _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under the victim has not settled for several epochs and holds a large userRewards balance, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `forfeitAmount` and `rewardInfo.rewardPerTokenStored` and the invariant that total forfeit must be invariant to how a user splits their settlements, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the victim has not settled for several epochs and holds a large userRewards balance, asserting on every row that total forfeit must be invariant to how a user splits their settlements.
