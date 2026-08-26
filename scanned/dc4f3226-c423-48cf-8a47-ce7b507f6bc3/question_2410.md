# Q2410: SmartWomConvert.depositFor - shared mWOM balance is settled to whoever calls next

## Question
wombat/SmartWomConvert.sol: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. With _amount and _for, with the mWOM pulled from the caller under attacker control and womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for)` so that `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))` no longer reconcile, violating the invariant that one caller must never be settled out of value another caller left behind and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, call `depositFor(uint256 _amount, address _for)`, and assert `obtainedmWomAmount` equals `IERC20(mWom).balanceOf(address(this))` and that no account can withdraw more than it put in.
