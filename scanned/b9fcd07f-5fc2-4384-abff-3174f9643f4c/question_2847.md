# Q2847: WombatStaking.withdraw - safeApprove without reset on the withdraw path

## Question
Note that in wombat/WombatStaking.sol, withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Can an attacker holding only tokens bought on market reach it via `withdraw(address,uint256,uint256,address) via a pool helper` under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit and force `totalAccumulated in mWOM` apart from `veWom balance of WombatStaking`, breaking the invariant that the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: safeApprove without reset on the withdraw path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() calls IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity) with no reset, so a Wombat pool withdrawal that leaves allowance behind bricks every subsequent withdrawal for the whole pool. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: the withdrawal path must remain usable regardless of allowance residue from an earlier partial spend; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(address,uint256,uint256,address) via a pool helper` sequence atomically under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, asserting at the end that `totalAccumulated in mWOM` still equals `veWom balance of WombatStaking` and the PoC's balance delta is non-positive.
