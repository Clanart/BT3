# Q1717: SmartWomConvert.depositFor - shared mWOM balance is settled to whoever calls next

## Question
wombat/SmartWomConvert.sol - mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Can an unprivileged attacker controlling _amount and _for, with the mWOM pulled from the caller, under the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, exploit this through `depositFor(uint256 _amount, address _for)` to break the reconciliation between `_minRec` and `convertAmount + amountRec` and the invariant that one caller must never be settled out of value another caller left behind, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, snapshot `_minRec` and `convertAmount + amountRec`, run the attacker's `depositFor(uint256 _amount, address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
