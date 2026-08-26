# Q4420: SmartWomConvert.depositFor - shared mWOM balance is settled to whoever calls next

## Question
In wombat/SmartWomConvert.sol, mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Starting from a state where a residual mWOM balance from an earlier rounding sits in the contract, can an unprivileged EOA use `depositFor(uint256 _amount, address _for)` to leave `amountRec from swapExactTokensForTokens` inconsistent with `convertAmount minted 1:1 by IMWom(mWom).deposit`, violating the invariant that one caller must never be settled out of value another caller left behind and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, with the mWOM pulled from the caller) under a residual mWOM balance from an earlier rounding sits in the contract, asserting on every row that one caller must never be settled out of value another caller left behind.
