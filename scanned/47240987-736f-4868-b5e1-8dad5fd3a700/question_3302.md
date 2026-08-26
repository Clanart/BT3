# Q3302: BaseRewardPoolV2.updateFor - totalStaked and balanceOf read from different sources

## Question
Consider rewards/BaseRewardPoolV2.sol, where totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Assuming the attacker funds the action with a flash loan of the staking token repaid in the same transaction, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` via `updateFor(address _account)`, breaking the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the attacker funds the action with a flash loan of the staking token repaid in the same transaction, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that sum over all accounts of balanceOf(account) must equal totalStaked().
