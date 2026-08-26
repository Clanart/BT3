# Q0844: mWOMSVBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/mWOMSVBaseRewarder.sol: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. With the victim address and the block at which their index is pinned under attacker control and the account's slot matured recently so the percent has only just begun to decay, can an unprivileged caller sequence `updateFor(address _account)` so that `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` no longer reconcile, violating the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the account's slot matured recently so the percent has only just begun to decay, then assert `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` end identical in both runs.
