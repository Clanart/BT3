# Q1291: BaseRewardPool.getRewards - duplicate reward tokens inside one getRewards array

## Question
rewards/BaseRewardPool.sol - getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Can an unprivileged attacker controlling the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor, under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, exploit this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` to break the reconciliation between `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and the invariant that settling the same reward token twice in one call must be equivalent to settling it once, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: duplicate reward tokens inside one getRewards array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: getRewards() iterates the caller's array with no uniqueness check, so the same reward token can be visited repeatedly within one settlement while userRewards and userRewardPerTokenPaid mutate between iterations. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: settling the same reward token twice in one call must be equivalent to settling it once; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, asserting at the end that `balanceOf(account)` still equals `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and the PoC's balance delta is non-positive.
