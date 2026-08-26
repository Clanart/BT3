# Q2184: ManualCompound.compound - helper branch deposits the whole balance for msg.sender

## Question
rewards/ManualCompound.sol: the ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender) branch credits the full contract balance of that token to the caller in MasterMagpie. Under the configured convertor is SmartWomConvert and _convertRatio is set to zero, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `_convertRatio supplied by the caller` unreconciled with `the value being converted for other users`, violates the invariant that a deposit branch must credit only the caller's own earned amount, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: helper branch deposits the whole balance for msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender) branch credits the full contract balance of that token to the caller in MasterMagpie. Precondition: the configured convertor is SmartWomConvert and _convertRatio is set to zero.
- Invariant to test: a deposit branch must credit only the caller's own earned amount; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the configured convertor is SmartWomConvert and _convertRatio is set to zero, have the attacker run `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, then assert the victim's claimable value and the `_convertRatio supplied by the caller` versus `the value being converted for other users` relation are unchanged by the attacker's transaction.
