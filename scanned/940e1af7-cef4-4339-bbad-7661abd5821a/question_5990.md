# Q5990: MasterMagpie.deposit - stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools

## Question
In rewards/MasterMagpie.sol, for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Starting from a state where the attacker splits the action across two transactions in the same block with a flash-loaned staking token, can an unprivileged EOA use `deposit(address _stakingToken, uint256 _amount)` to leave `totalAllocPoint` inconsistent with `tokenToPoolInfo[_stakingToken].allocPoint`, violating the invariant that sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Precondition: the attacker splits the action across two transactions in the same block with a flash-loaned staking token.
- Invariant to test: sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker splits the action across two transactions in the same block with a flash-loaned staking token, then assert `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` end identical in both runs.
