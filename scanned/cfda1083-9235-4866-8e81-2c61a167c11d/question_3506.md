# Q3506: BaseRewardPoolV2.getRewards - attacker-chosen reward-token array reaches getRewards

## Question
Consider rewards/BaseRewardPoolV2.sol, where MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Assuming the attacker funds the action with a flash loan of the staking token repaid in the same transaction, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that the set of tokens settled during a claim must not change the total value the claimer is entitled to and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token array reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec / multiclaimFor
- Exploit idea: MasterMagpie._multiClaim passes the caller's _rewardTokens[i] straight into getRewards(), so the attacker controls which tokens are settled, in which order, and which are deliberately omitted while rewardDebt is advanced anyway. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: the set of tokens settled during a claim must not change the total value the claimer is entitled to; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker funds the action with a flash loan of the staking token repaid in the same transaction, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `rewards[_rewardToken].rewardPerTokenStored` versus `userRewardPerTokenPaid[_rewardToken][account]` relation are unchanged by the attacker's transaction.
