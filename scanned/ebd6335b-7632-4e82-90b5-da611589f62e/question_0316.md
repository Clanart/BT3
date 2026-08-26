# Q0316: vlMGPBaseRewarder.getRewards - attacker-chosen reward-token list reaches getRewards

## Question
In rewards/vlMGPBaseRewarder.sol, MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Starting from a state where the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `totalStaked()` inconsistent with `IERC20(vlMGP).totalSupply()`, violating the invariant that the set of tokens a claimer names must not change the total value they are entitled to and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token list reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: the set of tokens a claimer names must not change the total value they are entitled to; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, snapshot `totalStaked()` and `IERC20(vlMGP).totalSupply()`, run the attacker's `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
