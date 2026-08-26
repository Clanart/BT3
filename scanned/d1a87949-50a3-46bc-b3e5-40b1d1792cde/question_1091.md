# Q1091: vlMGPBaseRewarder.getRewards - attacker-chosen reward-token list reaches getRewards

## Question
Consider rewards/vlMGPBaseRewarder.sol, where MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Assuming the account's slot matured recently so the percent has only just begun to decay, can an unprivileged attacker turn this into a divergence between `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that the set of tokens a claimer names must not change the total value they are entitled to and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token list reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: the set of tokens a claimer names must not change the total value they are entitled to; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the account's slot matured recently so the percent has only just begun to decay, asserting on every row that the set of tokens a claimer names must not change the total value they are entitled to.
