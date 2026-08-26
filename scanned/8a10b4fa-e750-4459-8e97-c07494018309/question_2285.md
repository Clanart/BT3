# Q2285: ArbWomUp3.incentiveDeposit - an unrecognised mode silently takes the plain transfer branch

## Question
Note that in wombat/ArbWomUp3.sol, _deposit() treats mode 1 as a SmartWomConvert deposit, mode 2 as the swap-and-lock path and anything else as a plain mWOM transfer, so an unexpected mode value falls through to the least restrictive settlement while the reward was priced for a different one. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under a residual mWOM balance from an earlier call sits on the contract and force `_convertRatio supplied by the caller` apart from `the buyback leg inside SmartWomConvert`, breaking the invariant that an unrecognised routing mode must revert rather than settle on the least restrictive branch for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: an unrecognised mode silently takes the plain transfer branch)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() treats mode 1 as a SmartWomConvert deposit, mode 2 as the swap-and-lock path and anything else as a plain mWOM transfer, so an unexpected mode value falls through to the least restrictive settlement while the reward was priced for a different one. Precondition: a residual mWOM balance from an earlier call sits on the contract.
- Invariant to test: an unrecognised routing mode must revert rather than settle on the least restrictive branch; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the buyback leg inside SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence atomically under a residual mWOM balance from an earlier call sits on the contract, asserting at the end that `_convertRatio supplied by the caller` still equals `the buyback leg inside SmartWomConvert` and the PoC's balance delta is non-positive.
