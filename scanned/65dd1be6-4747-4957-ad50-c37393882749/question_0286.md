# Q0286: mWOMSVBaseRewarder.getRewards - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/mWOMSVBaseRewarder.sol - totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Can an unprivileged attacker controlling the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor, under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, exploit this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` to break the reconciliation between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` and the invariant that sum of balanceOf over all accounts must equal totalStaked at all times, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, asserting at the end that `userRewards[_rewardToken][account]` still equals `rewards[_rewardToken].rewardPerTokenStored` and the PoC's balance delta is non-positive.
