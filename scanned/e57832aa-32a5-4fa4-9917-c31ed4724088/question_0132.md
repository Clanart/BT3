# Q0132: WombatStaking.convertWOM - veWOM lock commits pooled WOM for lockDays with no user opt-out

## Question
In wombat/WombatStaking.sol, convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Starting from a state where the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, can an unprivileged EOA use `convertWOM(uint256 _amount)` to leave `IERC20(poolInfo.lpAddress).balanceOf(address(this))` inconsistent with `lpReceived credited by IMintableERC20(receiptToken).mint`, violating the invariant that the backing of a liquid wrapper must remain redeemable under terms its holders accepted and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: veWOM lock commits pooled WOM for lockDays with no user opt-out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: the backing of a liquid wrapper must remain redeemable under terms its holders accepted; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, snapshot `IERC20(poolInfo.lpAddress).balanceOf(address(this))` and `lpReceived credited by IMintableERC20(receiptToken).mint`, run the attacker's `convertWOM(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
