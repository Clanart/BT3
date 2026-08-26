# Q2379: BaseRewardPool.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
In rewards/BaseRewardPool.sol, MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Starting from a state where the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `rewards[_rewardToken].queuedRewards` inconsistent with `rewards[_rewardToken].rewardPerTokenStored`, violating the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, call `getRewards(address _account, address _receiver, address[] _rewardTokens)`, and assert `rewards[_rewardToken].queuedRewards` equals `rewards[_rewardToken].rewardPerTokenStored` and that no account can withdraw more than it put in.
