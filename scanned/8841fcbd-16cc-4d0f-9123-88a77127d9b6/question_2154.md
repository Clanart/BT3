# Q2154: mWOMSVBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/mWOMSVBaseRewarder.sol: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Under the computed forfeit lands just above the _amount / 1000 dust threshold, is there an unprivileged sequence of `updateFor(address _account)` that leaves `balanceOf(account)` unreconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, violates the invariant that sum of balanceOf over all accounts must equal totalStaked at all times, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the block at which their index is pinned) under the computed forfeit lands just above the _amount / 1000 dust threshold, asserting on every row that sum of balanceOf over all accounts must equal totalStaked at all times.
