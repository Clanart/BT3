# Q4654: vlMGPBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
In rewards/vlMGPBaseRewarder.sol, totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Starting from a state where a registered reward token has begun reverting on transfer, can an unprivileged EOA use `updateFor(address _account)` to leave `balanceOf(account)` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, violating the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the block at which their index is pinned) under a registered reward token has begun reverting on transfer, asserting on every row that sum of balanceOf over all accounts must equal totalStaked at all times.
