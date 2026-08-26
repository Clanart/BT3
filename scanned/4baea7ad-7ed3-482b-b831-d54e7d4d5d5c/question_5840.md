# Q5840: MasterMagpie.deposit - stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools

## Question
In rewards/MasterMagpie.sol, for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Does `deposit(address _stakingToken, uint256 _amount)` let an unprivileged caller exploit that under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, so that `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` diverges from `block.timestamp`, the invariant that sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, have the attacker run `deposit(address _stakingToken, uint256 _amount)`, then assert the victim's claimable value and the `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` versus `block.timestamp` relation are unchanged by the attacker's transaction.
