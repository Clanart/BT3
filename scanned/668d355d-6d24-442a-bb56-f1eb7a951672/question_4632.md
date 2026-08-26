# Q4632: WombatStaking.withdraw - safeApprove without reset on the withdraw path

## Question
wombat/WombatStaking.sol: withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Under the deposit token for the pool is wBNB and the helper arrived through depositNative, is there an unprivileged sequence of `withdraw(address,uint256,uint256,address) via a pool helper` that leaves `isPoolFeeFree[_lpToken]` unreconciled with `feeInfos.length`, violates the invariant that the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: safeApprove without reset on the withdraw path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the deposit token for the pool is wBNB and the helper arrived through depositNative, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `isPoolFeeFree[_lpToken]` equals `feeInfos.length` and that no account can withdraw more than it put in.
