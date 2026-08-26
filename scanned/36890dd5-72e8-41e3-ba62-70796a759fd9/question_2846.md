# Q2846: mWOMSVBaseRewarder.getRewards - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/mWOMSVBaseRewarder.sol - totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Can an unprivileged attacker controlling the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor, under a large MGP distribution has just been queued and no account has settled yet, exploit this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` to break the reconciliation between `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` and the invariant that sum of balanceOf over all accounts must equal totalStaked at all times, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under a large MGP distribution has just been queued and no account has settled yet, asserting on every row that sum of balanceOf over all accounts must equal totalStaked at all times.
