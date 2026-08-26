# Q3987: WombatStaking.convertWOM - safeApprove without reset on the veWOM path

## Question
In wombat/WombatStaking.sol, convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Does `convertWOM(uint256 _amount)` let an unprivileged caller exploit that under several feeInfos entries are active at once and the harvested amount is small, so that `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` diverges from `_liquidity burned from the receipt token`, the invariant that an approval on a hot path must be idempotent and must not be able to permanently disable conversion is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: safeApprove without reset on the veWOM path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: an approval on a hot path must be idempotent and must not be able to permanently disable conversion; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish several feeInfos entries are active at once and the harvested amount is small, have the attacker run `convertWOM(uint256 _amount)`, then assert the victim's claimable value and the `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` versus `_liquidity burned from the receipt token` relation are unchanged by the attacker's transaction.
