# Q3289: mWOMSVBaseRewarder.getRewards - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/mWOMSVBaseRewarder.sol: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `_calExpireForfeit(account,_amount)` unreconciled with `mWOMSV.getRewardablePercentWAD(account)`, violates the invariant that sum of balanceOf over all accounts must equal totalStaked at all times, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor), and assert after every call that sum of balanceOf over all accounts must equal totalStaked at all times.
