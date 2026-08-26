# Q4790: SmartWomConvert.depositFor - shared mWOM balance is settled to whoever calls next

## Question
In wombat/SmartWomConvert.sol, mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Does `depositFor(uint256 _amount, address _for)` let an unprivileged caller exploit that under the attacker sandwiches the transaction on the wom/mWom Wombat pool, so that `_minRec` diverges from `convertAmount + amountRec`, the invariant that one caller must never be settled out of value another caller left behind is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, with the mWOM pulled from the caller) under the attacker sandwiches the transaction on the wom/mWom Wombat pool, asserting on every row that one caller must never be settled out of value another caller left behind.
