# Q4235: mWOMSVBaseRewarder.getReward - totalStaked and balanceOf drawn from unrelated sources

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Assuming the attacker locks one block before a known large settlement and unlocks one block after, can an unprivileged attacker turn this into a divergence between `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` via `getReward(address _account, address _receiver)`, breaking the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the attacker locks one block before a known large settlement and unlocks one block after, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `balanceOf(account)` versus `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` relation are unchanged by the attacker's transaction.
