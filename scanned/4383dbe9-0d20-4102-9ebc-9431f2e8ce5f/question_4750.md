# Q4750: vlMGPBaseRewarder.getRewards - attacker-chosen reward-token list reaches getRewards

## Question
In rewards/vlMGPBaseRewarder.sol, MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Does `getRewards(address _account, address _receiver, address[] _rewardTokens)` let an unprivileged caller exploit that under a registered reward token has begun reverting on transfer, so that `rewards[_rewardToken].historicalRewards` diverges from `IERC20(_rewardToken).balanceOf(address(this))`, the invariant that the set of tokens a claimer names must not change the total value they are entitled to is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token list reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: the set of tokens a claimer names must not change the total value they are entitled to; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a registered reward token has begun reverting on transfer, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `rewards[_rewardToken].historicalRewards` versus `IERC20(_rewardToken).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
