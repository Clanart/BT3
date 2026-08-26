# Q1061: mWOMSVBaseRewarder.getRewards - totalStaked and balanceOf drawn from unrelated sources

## Question
In rewards/mWOMSVBaseRewarder.sol, totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Starting from a state where the account's slot matured recently so the percent has only just begun to decay, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `totalStaked()` inconsistent with `IERC20(mWOMSV).totalSupply()`, violating the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the account's slot matured recently so the percent has only just begun to decay, then assert `totalStaked()` and `IERC20(mWOMSV).totalSupply()` end identical in both runs.
