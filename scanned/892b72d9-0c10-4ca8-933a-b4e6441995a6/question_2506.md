# Q2506: ManualCompound.compound - helper branch deposits the whole balance for msg.sender

## Question
rewards/ManualCompound.sol: the ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender) branch credits the full contract balance of that token to the caller in MasterMagpie. With every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls under attacker control and the configured convertor is SmartWomConvert and _minRec is set to zero, can an unprivileged caller sequence `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` so that `_minRec supplied by the caller` and `obtainedmWomAmount in SmartWomConvert` no longer reconcile, violating the invariant that a deposit branch must credit only the caller's own earned amount and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: helper branch deposits the whole balance for msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender) branch credits the full contract balance of that token to the caller in MasterMagpie. Precondition: the configured convertor is SmartWomConvert and _minRec is set to zero.
- Invariant to test: a deposit branch must credit only the caller's own earned amount; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under the configured convertor is SmartWomConvert and _minRec is set to zero, asserting on every row that a deposit branch must credit only the caller's own earned amount.
