# Q2315: mWOMSVBaseRewarder.getRewards - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/mWOMSVBaseRewarder.sol: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. With the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor under attacker control and the computed forfeit lands just above the _amount / 1000 dust threshold, can an unprivileged caller sequence `getRewards(address _account, address _receiver, address[] _rewardTokens)` so that `forfeitAmount` and `rewardInfo.rewardPerTokenStored` no longer reconcile, violating the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the computed forfeit lands just above the _amount / 1000 dust threshold, asserting on every row that sum of balanceOf over all accounts must equal totalStaked at all times.
