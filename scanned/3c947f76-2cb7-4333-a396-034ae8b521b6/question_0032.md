# Q0032: MasterMagpie.deposit - stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools

## Question
In rewards/MasterMagpie.sol, for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Does `deposit(address _stakingToken, uint256 _amount)` let an unprivileged caller exploit that under the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, so that `_calLpSupply(_stakingToken)` diverges from `IERC20(_stakingToken).balanceOf(masterMagpie)`, the invariant that sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, then assert `_calLpSupply(_stakingToken)` and `IERC20(_stakingToken).balanceOf(masterMagpie)` end identical in both runs.
