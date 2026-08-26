# Q2922: mWOMSVBaseRewarder.getRewards - forfeit computed on the full userRewards on every partial settlement

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under a large MGP distribution has just been queued and no account has settled yet and force `rewards[_rewardToken].historicalRewards` apart from `IERC20(_rewardToken).balanceOf(address(this))`, breaking the invariant that total forfeit must be invariant to how a user splits their settlements for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit computed on the full userRewards on every partial settlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _sendReward() prices the forfeit against the entire userRewards balance for that token each time it runs, so splitting or repeating settlements changes how much value passes through the forfeit rule. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: total forfeit must be invariant to how a user splits their settlements; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under a large MGP distribution has just been queued and no account has settled yet, asserting at the end that `rewards[_rewardToken].historicalRewards` still equals `IERC20(_rewardToken).balanceOf(address(this))` and the PoC's balance delta is non-positive.
