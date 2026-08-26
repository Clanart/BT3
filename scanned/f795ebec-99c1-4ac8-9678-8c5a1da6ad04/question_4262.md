# Q4262: WombatStaking.withdraw - safeApprove without reset on the withdraw path

## Question
In wombat/WombatStaking.sol, withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Does `withdraw(address,uint256,uint256,address) via a pool helper` let an unprivileged caller exploit that under several feeInfos entries are active at once and the harvested amount is small, so that `womRewards measured by balance delta` diverges from `the amount queued into poolInfo.rewarder`, the invariant that the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: safeApprove without reset on the withdraw path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange several feeInfos entries are active at once and the harvested amount is small, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `womRewards measured by balance delta` equals `the amount queued into poolInfo.rewarder` and that no account can withdraw more than it put in.
