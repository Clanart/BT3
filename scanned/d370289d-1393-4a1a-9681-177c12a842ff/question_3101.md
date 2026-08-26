# Q3101: vlMGPBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
Consider rewards/vlMGPBaseRewarder.sol, where _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Assuming a large MGP distribution has just been queued and no account has settled yet, can an unprivileged attacker turn this into a divergence between `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` via `getReward(address _account, address _receiver)`, breaking the invariant that total forfeit must be invariant to how a user splits their settlements and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large MGP distribution has just been queued and no account has settled yet, call `getReward(address _account, address _receiver)`, and assert `_calExpireForfeit(account,_amount)` equals `vlMGP.getRewardablePercentWAD(account)` and that no account can withdraw more than it put in.
