# Q3306: mWOMSVBaseRewarder.getRewards - attacker-chosen reward-token list reaches getRewards

## Question
rewards/mWOMSVBaseRewarder.sol: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. With the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor under attacker control and the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged caller sequence `getRewards(address _account, address _receiver, address[] _rewardTokens)` so that `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` no longer reconcile, violating the invariant that the set of tokens a claimer names must not change the total value they are entitled to and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token list reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: the set of tokens a claimer names must not change the total value they are entitled to; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, asserting on every row that the set of tokens a claimer names must not change the total value they are entitled to.
