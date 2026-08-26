# Q2866: WombatStaking.withdraw - withdraw pays out a balance delta rather than a computed entitlement

## Question
In wombat/WombatStaking.sol, withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Starting from a state where smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, can an unprivileged EOA use `withdraw(address,uint256,uint256,address) via a pool helper` to leave `feeInfos[i].value` inconsistent with `totalFee`, violating the invariant that a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: withdraw pays out a balance delta rather than a computed entitlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(address,uint256,uint256,address) via a pool helper` sequence atomically under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, asserting at the end that `feeInfos[i].value` still equals `totalFee` and the PoC's balance delta is non-positive.
