# Q2622: WomUp.getReward - no reentrancy guard on any balance-mutating function

## Question
wombat/WomUp.sol: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. With the exact block at which accrued MGP is locked into vlMGP for the caller under attacker control and the MGP balance is below the sum of accrued rewards, can an unprivileged caller sequence `getReward()` so that `_balances[account]` and `_totalSupply` no longer reconcile, violating the invariant that every function that mutates the stake ledger must share one reentrancy domain and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `getReward()` sequence atomically under the MGP balance is below the sum of accrued rewards, asserting at the end that `_balances[account]` still equals `_totalSupply` and the PoC's balance delta is non-positive.
