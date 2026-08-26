# Q4967: WombatStaking.withdraw - safeApprove without reset on the withdraw path

## Question
Note that in wombat/WombatStaking.sol, withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Can an attacker holding only tokens bought on market reach it via `withdraw(address,uint256,uint256,address) via a pool helper` under the attacker deposits and withdraws through the same helper inside one transaction and force `IERC20(poolInfo.lpAddress).balanceOf(address(this))` apart from `lpReceived credited by IMintableERC20(receiptToken).mint`, breaking the invariant that the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: safeApprove without reset on the withdraw path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker deposits and withdraws through the same helper inside one transaction, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `IERC20(poolInfo.lpAddress).balanceOf(address(this))` equals `lpReceived credited by IMintableERC20(receiptToken).mint` and that no account can withdraw more than it put in.
