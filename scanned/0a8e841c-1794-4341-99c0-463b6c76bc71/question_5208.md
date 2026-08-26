# Q5208: WombatStaking.withdraw - safeApprove without reset on the withdraw path

## Question
wombat/WombatStaking.sol: withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Under a large honest deposit is pending in the mempool for the same pool, is there an unprivileged sequence of `withdraw(address,uint256,uint256,address) via a pool helper` that leaves `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` unreconciled with `_liquidity burned from the receipt token`, violates the invariant that the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: safeApprove without reset on the withdraw path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish a large honest deposit is pending in the mempool for the same pool, have the attacker run `withdraw(address,uint256,uint256,address) via a pool helper`, then assert the victim's claimable value and the `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` versus `_liquidity burned from the receipt token` relation are unchanged by the attacker's transaction.
