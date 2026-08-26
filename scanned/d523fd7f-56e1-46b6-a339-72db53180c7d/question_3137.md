# Q3137: WombatStaking.harvest - harvest routes protocol WOM through a spot-priced smart convert

## Question
In wombat/WombatStaking.sol, _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Does `harvest(address _lpToken)` let an unprivileged caller exploit that under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, so that `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` diverges from `_liquidity burned from the receipt token`, the invariant that protocol-owned value must not be traded at a price a caller can set in the same transaction is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest routes protocol WOM through a spot-priced smart convert)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: protocol-owned value must not be traded at a price a caller can set in the same transaction; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, call `harvest(address _lpToken)`, and assert `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` equals `_liquidity burned from the receipt token` and that no account can withdraw more than it put in.
