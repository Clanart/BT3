# Q2245: vlMGPBaseRewarder.getRewards - dust threshold waives the forfeit entirely

## Question
Note that in rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under the computed forfeit lands just above the _amount / 1000 dust threshold and force `rewards[_rewardToken].historicalRewards` apart from `IERC20(_rewardToken).balanceOf(address(this))`, breaking the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the computed forfeit lands just above the _amount / 1000 dust threshold, call `getRewards(address _account, address _receiver, address[] _rewardTokens)`, and assert `rewards[_rewardToken].historicalRewards` equals `IERC20(_rewardToken).balanceOf(address(this))` and that no account can withdraw more than it put in.
