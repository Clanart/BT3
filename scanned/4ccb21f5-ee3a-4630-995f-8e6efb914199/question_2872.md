# Q2872: ManualCompound.compound - rewards array iterated for every caller regardless of what they claimed

## Question
In rewards/ManualCompound.sol, the second loop iterates the full rewards array on every call, so every configured reward token is swept on every invocation even when the caller's claim touched none of them. Starting from a state where _lockMgp is true and a locker is configured for the MGP entry, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `_minRec supplied by the caller` inconsistent with `obtainedmWomAmount in SmartWomConvert`, violating the invariant that settlement must be scoped to the reward tokens the caller's claim actually produced and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: rewards array iterated for every caller regardless of what they claimed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the second loop iterates the full rewards array on every call, so every configured reward token is swept on every invocation even when the caller's claim touched none of them. Precondition: _lockMgp is true and a locker is configured for the MGP entry.
- Invariant to test: settlement must be scoped to the reward tokens the caller's claim actually produced; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under _lockMgp is true and a locker is configured for the MGP entry, asserting on every row that settlement must be scoped to the reward tokens the caller's claim actually produced.
