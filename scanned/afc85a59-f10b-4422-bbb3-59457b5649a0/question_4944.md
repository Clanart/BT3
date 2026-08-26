# Q4944: mWOMSVBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
In rewards/mWOMSVBaseRewarder.sol, totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Does `updateFor(address _account)` let an unprivileged caller exploit that under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, so that `forfeitAmount` diverges from `rewardInfo.rewardPerTokenStored`, the invariant that sum of balanceOf over all accounts must equal totalStaked at all times is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, then assert `forfeitAmount` and `rewardInfo.rewardPerTokenStored` end identical in both runs.
