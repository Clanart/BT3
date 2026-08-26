# Q4640: BaseRewardPool.getRewards - duplicate reward tokens inside one getRewards array

## Question
rewards/BaseRewardPool.sol: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. With the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor under attacker control and the reward token charges a transfer fee so the received balance is below the requested amount, can an unprivileged caller sequence `getRewards(address _account, address _receiver, address[] _rewardTokens)` so that `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` no longer reconcile, violating the invariant that settling the same reward token twice in one call must be equivalent to settling it once and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: duplicate reward tokens inside one getRewards array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: settling the same reward token twice in one call must be equivalent to settling it once; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the reward token charges a transfer fee so the received balance is below the requested amount, call `getRewards(address _account, address _receiver, address[] _rewardTokens)`, and assert `balanceOf(account)` equals `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and that no account can withdraw more than it put in.
