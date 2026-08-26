# Q1970: mWOMSVBaseRewarder.getReward - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/mWOMSVBaseRewarder.sol: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and the computed forfeit lands just below the _amount / 1000 dust threshold, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `forfeitAmount` and `rewardInfo.rewardPerTokenStored` no longer reconcile, violating the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the computed forfeit lands just below the _amount / 1000 dust threshold, then assert `forfeitAmount` and `rewardInfo.rewardPerTokenStored` end identical in both runs.
