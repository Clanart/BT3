# Q1458: mWOMSVBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
rewards/mWOMSVBaseRewarder.sol: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Under the account's slot matured recently so the percent has only just begun to decay, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `balanceOf(account)` unreconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, violates the invariant that total forfeit must be invariant to how a user splits their settlements, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the account's slot matured recently so the percent has only just begun to decay, snapshot `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
