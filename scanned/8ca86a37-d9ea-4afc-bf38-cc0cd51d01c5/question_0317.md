# Q0317: mWOMSVBaseRewarder.getRewards - attacker-chosen reward-token list reaches getRewards

## Question
rewards/mWOMSVBaseRewarder.sol - MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Can an unprivileged attacker controlling the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor, under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, exploit this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` to break the reconciliation between `totalStaked()` and `IERC20(mWOMSV).totalSupply()` and the invariant that the set of tokens a claimer names must not change the total value they are entitled to, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token list reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: the set of tokens a claimer names must not change the total value they are entitled to; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, call `getRewards(address _account, address _receiver, address[] _rewardTokens)`, and assert `totalStaked()` equals `IERC20(mWOMSV).totalSupply()` and that no account can withdraw more than it put in.
