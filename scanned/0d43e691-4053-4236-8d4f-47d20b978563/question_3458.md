# Q3458: vlMGPBaseRewarder.getReward - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/vlMGPBaseRewarder.sol: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` no longer reconcile, violating the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, then assert `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` end identical in both runs.
