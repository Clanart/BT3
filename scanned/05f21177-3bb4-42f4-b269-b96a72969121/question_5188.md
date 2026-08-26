# Q5188: SmartWomConvert.convertFor - shared mWOM balance is settled to whoever calls next

## Question
wombat/SmartWomConvert.sol: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. With _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound under attacker control and _convertRatio is set to zero so the entire input goes through the AMM, can an unprivileged caller sequence `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` so that `_minRec` and `convertAmount + amountRec` no longer reconcile, violating the invariant that one caller must never be settled out of value another caller left behind and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up _convertRatio is set to zero so the entire input goes through the AMM, snapshot `_minRec` and `convertAmount + amountRec`, run the attacker's `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
