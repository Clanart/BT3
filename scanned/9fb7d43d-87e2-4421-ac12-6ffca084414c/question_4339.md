# Q4339: mWOMSVBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
In rewards/mWOMSVBaseRewarder.sol, totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Can an unprivileged attacker reach this through `updateFor(address _account)` while the victim has not settled for several epochs and holds a large userRewards balance, and drive `totalStaked()` out of agreement with `IERC20(mWOMSV).totalSupply()` - breaking the invariant that sum of balanceOf over all accounts must equal totalStaked at all times - for Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the victim has not settled for several epochs and holds a large userRewards balance, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(mWOMSV).totalSupply()` relation are unchanged by the attacker's transaction.
