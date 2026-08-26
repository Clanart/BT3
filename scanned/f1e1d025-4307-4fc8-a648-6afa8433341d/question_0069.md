# Q0069: mWOMSVBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Assuming the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged attacker turn this into a divergence between `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` via `updateFor(address _account)`, breaking the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, asserting at the end that `_calExpireForfeit(account,_amount)` still equals `mWOMSV.getRewardablePercentWAD(account)` and the PoC's balance delta is non-positive.
