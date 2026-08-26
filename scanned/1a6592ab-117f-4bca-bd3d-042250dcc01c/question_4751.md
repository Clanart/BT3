# Q4751: mWOMSVBaseRewarder.getRewards - attacker-chosen reward-token list reaches getRewards

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under a registered reward token has begun reverting on transfer and force `rewards[_rewardToken].historicalRewards` apart from `IERC20(_rewardToken).balanceOf(address(this))`, breaking the invariant that the set of tokens a claimer names must not change the total value they are entitled to for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token list reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: the set of tokens a claimer names must not change the total value they are entitled to; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under a registered reward token has begun reverting on transfer, asserting on every row that the set of tokens a claimer names must not change the total value they are entitled to.
