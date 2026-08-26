# Q2009: ArbWomUp3.incentiveDeposit - an unrecognised mode silently takes the plain transfer branch

## Question
Consider wombat/ArbWomUp3.sol, where _deposit() treats mode 1 as a SmartWomConvert deposit, mode 2 as the swap-and-lock path and anything else as a plain mWOM transfer, so an unexpected mode value falls through to the least restrictive settlement while the reward was priced for a different one. Assuming the caller sandwiches the wom/mWom Wombat pool around the transaction, can an unprivileged attacker turn this into a divergence between `bracketRewarded` and `calDoubledCounted(account)` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that an unrecognised routing mode must revert rather than settle on the least restrictive branch and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: an unrecognised mode silently takes the plain transfer branch)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() treats mode 1 as a SmartWomConvert deposit, mode 2 as the swap-and-lock path and anything else as a plain mWOM transfer, so an unexpected mode value falls through to the least restrictive settlement while the reward was priced for a different one. Precondition: the caller sandwiches the wom/mWom Wombat pool around the transaction.
- Invariant to test: an unrecognised routing mode must revert rather than settle on the least restrictive branch; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the caller sandwiches the wom/mWom Wombat pool around the transaction, snapshot `bracketRewarded` and `calDoubledCounted(account)`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
