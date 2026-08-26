# Q2035: BaseRewardPoolV2.getRewards - duplicate reward tokens inside one getRewards array

## Question
rewards/BaseRewardPoolV2.sol - getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Can an unprivileged attacker controlling the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor, under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, exploit this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` to break the reconciliation between `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` and the invariant that settling the same reward token twice in one call must be equivalent to settling it once, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: duplicate reward tokens inside one getRewards array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: settling the same reward token twice in one call must be equivalent to settling it once; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, call `getRewards(address _account, address _receiver, address[] _rewardTokens)`, and assert `rewards[_rewardToken].historicalRewards` equals `IERC20(_rewardToken).balanceOf(address(this))` and that no account can withdraw more than it put in.
