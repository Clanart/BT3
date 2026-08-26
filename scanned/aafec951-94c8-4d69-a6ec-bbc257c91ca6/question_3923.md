# Q3923: BaseRewardPoolV2.updateFor - totalStaked and balanceOf read from different sources

## Question
rewards/BaseRewardPoolV2.sol: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Under the reward token charges a transfer fee so the received balance is below the requested amount, is there an unprivileged sequence of `updateFor(address _account)` that leaves `10**stakingDecimals()` unreconciled with `totalStaked()`, violates the invariant that sum over all accounts of balanceOf(account) must equal totalStaked(), and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the reward token charges a transfer fee so the received balance is below the requested amount, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that sum over all accounts of balanceOf(account) must equal totalStaked().
