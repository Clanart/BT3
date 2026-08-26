# Q3986: mWOMSVBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
rewards/mWOMSVBaseRewarder.sol: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Under the attacker locks one block before a known large settlement and unlocks one block after, is there an unprivileged sequence of `updateFor(address _account)` that leaves `userRewards[_rewardToken][account]` unreconciled with `rewards[_rewardToken].rewardPerTokenStored`, violates the invariant that sum of balanceOf over all accounts must equal totalStaked at all times, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(mWOMSV).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker locks one block before a known large settlement and unlocks one block after, call `updateFor(address _account)`, and assert `userRewards[_rewardToken][account]` equals `rewards[_rewardToken].rewardPerTokenStored` and that no account can withdraw more than it put in.
