# Q5760: MasterMagpie.deposit - stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools

## Question
In rewards/MasterMagpie.sol, for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Does `deposit(address _stakingToken, uint256 _amount)` let an unprivileged caller exploit that under the victim has a large unClaimedMgp balance that has not been settled for several epochs, so that `_calLpSupply(_stakingToken)` diverges from `IERC20(_stakingToken).balanceOf(masterMagpie)`, the invariant that sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unClaimedMgp balance that has not been settled for several epochs, call `deposit(address _stakingToken, uint256 _amount)`, and assert `_calLpSupply(_stakingToken)` equals `IERC20(_stakingToken).balanceOf(masterMagpie)` and that no account can withdraw more than it put in.
