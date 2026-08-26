# Q1350: vlMGPBaseRewarder.getReward - totalStaked and balanceOf drawn from unrelated sources

## Question
In rewards/vlMGPBaseRewarder.sol, totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under the account's slot matured recently so the percent has only just begun to decay, so that `balanceOf(account)` diverges from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, the invariant that sum of balanceOf over all accounts must equal totalStaked at all times is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the account's slot matured recently so the percent has only just begun to decay, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `balanceOf(account)` versus `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` relation are unchanged by the attacker's transaction.
