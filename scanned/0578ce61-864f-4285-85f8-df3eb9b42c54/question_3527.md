# Q3527: mWOMSVBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
rewards/mWOMSVBaseRewarder.sol - _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` and the invariant that total forfeit must be invariant to how a user splits their settlements, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, call `getReward(address _account, address _receiver)`, and assert `userRewards[_rewardToken][account]` equals `rewards[_rewardToken].rewardPerTokenStored` and that no account can withdraw more than it put in.
