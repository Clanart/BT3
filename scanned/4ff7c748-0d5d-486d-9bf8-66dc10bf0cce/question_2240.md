# Q2240: MasterMagpie.deposit - stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools

## Question
In rewards/MasterMagpie.sol, for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Does `deposit(address _stakingToken, uint256 _amount)` let an unprivileged caller exploit that under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, so that `IBaseRewardPool(rewarder).balanceOf(user)` diverges from `IBaseRewardPool(rewarder).totalStaked()`, the invariant that sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, call `deposit(address _stakingToken, uint256 _amount)`, and assert `IBaseRewardPool(rewarder).balanceOf(user)` equals `IBaseRewardPool(rewarder).totalStaked()` and that no account can withdraw more than it put in.
