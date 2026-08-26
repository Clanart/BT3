# Q4118: BaseRewardPoolV2.getRewards - duplicate reward tokens inside one getRewards array

## Question
rewards/BaseRewardPoolV2.sol: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Under the reward token charges a transfer fee so the received balance is below the requested amount, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `balanceOf(account)` unreconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, violates the invariant that settling the same reward token twice in one call must be equivalent to settling it once, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: duplicate reward tokens inside one getRewards array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: settling the same reward token twice in one call must be equivalent to settling it once; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under the reward token charges a transfer fee so the received balance is below the requested amount, asserting at the end that `balanceOf(account)` still equals `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and the PoC's balance delta is non-positive.
