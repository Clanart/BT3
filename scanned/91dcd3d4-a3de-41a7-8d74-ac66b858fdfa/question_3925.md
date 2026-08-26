# Q3925: vlMGPBaseRewarder.getReward - forfeit computed on the full userRewards on every partial settlement

## Question
Consider rewards/vlMGPBaseRewarder.sol, where _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Assuming totalStaked is zero and queuedRewards holds a backlog, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(vlMGP).totalSupply()` via `getReward(address _account, address _receiver)`, breaking the invariant that total forfeit must be invariant to how a user splits their settlements and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange totalStaked is zero and queuedRewards holds a backlog, call `getReward(address _account, address _receiver)`, and assert `totalStaked()` equals `IERC20(vlMGP).totalSupply()` and that no account can withdraw more than it put in.
