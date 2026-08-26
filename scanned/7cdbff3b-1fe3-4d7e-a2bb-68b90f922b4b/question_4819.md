# Q4819: MasterMagpie.deposit - stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools

## Question
In rewards/MasterMagpie.sol, for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Can an unprivileged attacker reach this through `deposit(address _stakingToken, uint256 _amount)` while the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, and drive `userInfo[_stakingToken][user].amount` out of agreement with `_calLpSupply(_stakingToken)` - breaking the invariant that sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times - for Critical - Protocol insolvency?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: stakingInfo(amount) versus totalStaked(balanceOf) divergence for vlMGP pools)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: for vlMGP and mWomSV pools _deposit() credits UserInfo.amount with no token transfer (_isVlmgp == true), while every BaseRewardPool prices rewards with totalStaked() = IERC20(stakingToken).balanceOf(operator), so the per-user numerator and the global denominator are drawn from two unrelated sources. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: sum(balanceOf(user)) over a rewarder must equal that rewarder's totalStaked() at all times; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, call `deposit(address _stakingToken, uint256 _amount)`, and assert `userInfo[_stakingToken][user].amount` equals `_calLpSupply(_stakingToken)` and that no account can withdraw more than it put in.
