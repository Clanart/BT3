# Q3021: ManualCompound.compound - converted output is directed to msg.sender

## Question
Consider rewards/ManualCompound.sol, where convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Assuming no convertor, locker or helper is configured for one of the registered rewards, can an unprivileged attacker turn this into a divergence between `_minRec supplied by the caller` and `obtainedmWomAmount in SmartWomConvert` via `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, breaking the invariant that converted value must be attributed to the account that earned it and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: converted output is directed to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Precondition: no convertor, locker or helper is configured for one of the registered rewards.
- Invariant to test: converted value must be attributed to the account that earned it; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under no convertor, locker or helper is configured for one of the registered rewards, then assert `_minRec supplied by the caller` and `obtainedmWomAmount in SmartWomConvert` end identical in both runs.
