# Q3740: WombatPoolHelperV2.deposit - depositFor hardcodes _minimumLiquidity to zero

## Question
In wombat/WombatPoolHelperV2.sol, depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Can an unprivileged attacker reach this through `deposit(uint256 _amount, uint256 _minimumLiquidity)` while the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, and drive `IERC20(stakingToken).balanceOf(address(this)) delta` out of agreement with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` - breaking the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, have the attacker run `deposit(uint256 _amount, uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `IERC20(stakingToken).balanceOf(address(this)) delta` versus `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` relation are unchanged by the attacker's transaction.
