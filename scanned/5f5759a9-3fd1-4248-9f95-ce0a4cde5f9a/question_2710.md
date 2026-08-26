# Q2710: mWOMSVBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
In rewards/mWOMSVBaseRewarder.sol, totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Starting from a state where a large MGP distribution has just been queued and no account has settled yet, can an unprivileged EOA use `updateFor(address _account)` to leave `forfeitAmount` inconsistent with `rewardInfo.rewardPerTokenStored`, violating the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large MGP distribution has just been queued and no account has settled yet, then assert `forfeitAmount` and `rewardInfo.rewardPerTokenStored` end identical in both runs.
