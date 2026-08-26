# Q3102: mWOMSVBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
In rewards/mWOMSVBaseRewarder.sol, _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Starting from a state where a large MGP distribution has just been queued and no account has settled yet, can an unprivileged EOA use `getReward(address _account, address _receiver)` to leave `_calExpireForfeit(account,_amount)` inconsistent with `mWOMSV.getRewardablePercentWAD(account)`, violating the invariant that total forfeit must be invariant to how a user splits their settlements and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that a large MGP distribution has just been queued and no account has settled yet, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that total forfeit must be invariant to how a user splits their settlements.
