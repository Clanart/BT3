# Q3590: vlMGPBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
In rewards/vlMGPBaseRewarder.sol, totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Starting from a state where totalStaked is zero and queuedRewards holds a backlog, can an unprivileged EOA use `updateFor(address _account)` to leave `_calExpireForfeit(account,_amount)` inconsistent with `vlMGP.getRewardablePercentWAD(account)`, violating the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under totalStaked is zero and queuedRewards holds a backlog, then assert `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` end identical in both runs.
