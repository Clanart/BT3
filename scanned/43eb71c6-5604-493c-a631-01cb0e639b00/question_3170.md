# Q3170: mWOMSVBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/mWOMSVBaseRewarder.sol: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, is there an unprivileged sequence of `updateFor(address _account)` that leaves `rewards[_rewardToken].historicalRewards` unreconciled with `IERC20(_rewardToken).balanceOf(address(this))`, violates the invariant that sum of balanceOf over all accounts must equal totalStaked at all times, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that sum of balanceOf over all accounts must equal totalStaked at all times.
