# Q0843: vlMGPBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
In rewards/vlMGPBaseRewarder.sol, totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Can an unprivileged attacker reach this through `updateFor(address _account)` while the account's slot matured recently so the percent has only just begun to decay, and drive `userRewards[_rewardToken][account]` out of agreement with `rewards[_rewardToken].rewardPerTokenStored` - breaking the invariant that sum of balanceOf over all accounts must equal totalStaked at all times - for Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the account's slot matured recently so the percent has only just begun to decay, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that sum of balanceOf over all accounts must equal totalStaked at all times.
