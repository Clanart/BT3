# Q2338: mWOMSVBaseRewarder.getRewards - attacker-chosen reward-token list reaches getRewards

## Question
rewards/mWOMSVBaseRewarder.sol: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. With the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor under attacker control and the computed forfeit lands just above the _amount / 1000 dust threshold, can an unprivileged caller sequence `getRewards(address _account, address _receiver, address[] _rewardTokens)` so that `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` no longer reconcile, violating the invariant that the set of tokens a claimer names must not change the total value they are entitled to and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token list reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: the set of tokens a claimer names must not change the total value they are entitled to; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that the computed forfeit lands just above the _amount / 1000 dust threshold, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor), and assert after every call that the set of tokens a claimer names must not change the total value they are entitled to.
