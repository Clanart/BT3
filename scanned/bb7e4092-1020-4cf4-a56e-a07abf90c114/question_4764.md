# Q4764: WombatStaking.convertWOM - veWOM lock commits pooled WOM for lockDays with no user opt-out

## Question
In wombat/WombatStaking.sol, convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Starting from a state where the attacker deposits and withdraws through the same helper inside one transaction, can an unprivileged EOA use `convertWOM(uint256 _amount)` to leave `IERC20(poolInfo.lpAddress).balanceOf(address(this))` inconsistent with `lpReceived credited by IMintableERC20(receiptToken).mint`, violating the invariant that the backing of a liquid wrapper must remain redeemable under terms its holders accepted and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: veWOM lock commits pooled WOM for lockDays with no user opt-out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: the backing of a liquid wrapper must remain redeemable under terms its holders accepted; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, with no upper bound and no relation to who supplied the WOM) under the attacker deposits and withdraws through the same helper inside one transaction, asserting on every row that the backing of a liquid wrapper must remain redeemable under terms its holders accepted.
