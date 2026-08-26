# Q1031: WombatStaking.convertWOM - veWOM lock commits pooled WOM for lockDays with no user opt-out

## Question
Note that in wombat/WombatStaking.sol, convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Can an attacker holding only tokens bought on market reach it via `convertWOM(uint256 _amount)` under the contract is holding WOM collected as a protocol fee that has not yet been split and force `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` apart from `_liquidity burned from the receipt token`, breaking the invariant that the backing of a liquid wrapper must remain redeemable under terms its holders accepted for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: veWOM lock commits pooled WOM for lockDays with no user opt-out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: the backing of a liquid wrapper must remain redeemable under terms its holders accepted; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `convertWOM(uint256 _amount)` sequence atomically under the contract is holding WOM collected as a protocol fee that has not yet been split, asserting at the end that `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` still equals `_liquidity burned from the receipt token` and the PoC's balance delta is non-positive.
