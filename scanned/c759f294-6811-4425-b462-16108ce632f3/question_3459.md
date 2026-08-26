# Q3459: mWOMSVBaseRewarder.getReward - totalStaked and balanceOf drawn from unrelated sources

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Assuming the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` via `getReward(address _account, address _receiver)`, breaking the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, asserting at the end that `userRewards[_rewardToken][account]` still equals `rewards[_rewardToken].rewardPerTokenStored` and the PoC's balance delta is non-positive.
