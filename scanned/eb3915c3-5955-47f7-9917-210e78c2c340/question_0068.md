# Q0068: vlMGPBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/vlMGPBaseRewarder.sol: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. With the victim address and the block at which their index is pinned under attacker control and the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged caller sequence `updateFor(address _account)` so that `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` no longer reconcile, violating the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, then assert `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` end identical in both runs.
