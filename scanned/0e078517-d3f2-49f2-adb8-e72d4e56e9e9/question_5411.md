# Q5411: WombatStaking.withdraw - safeApprove without reset on the withdraw path

## Question
Consider wombat/WombatStaking.sol, where withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Assuming the bonus reward token registered for the asset is also one of the fee currencies, can an unprivileged attacker turn this into a divergence between `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` via `withdraw(address,uint256,uint256,address) via a pool helper`, breaking the invariant that the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: safeApprove without reset on the withdraw path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw) under the bonus reward token registered for the asset is also one of the fee currencies, asserting on every row that the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend.
