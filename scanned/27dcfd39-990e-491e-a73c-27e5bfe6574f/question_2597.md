# Q2597: ManualCompound.compound - rewards array iterated for every caller regardless of what they claimed

## Question
In rewards/ManualCompound.sol, the second loop iterates the full rewards array on every call, so every configured reward token is swept on every invocation even when the caller's claim touched none of them. Can an unprivileged attacker reach this through `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` while the configured convertor is SmartWomConvert and _minRec is set to zero, and drive `_convertRatio supplied by the caller` out of agreement with `the value being converted for other users` - breaking the invariant that settlement must be scoped to the reward tokens the caller's claim actually produced - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: rewards array iterated for every caller regardless of what they claimed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the second loop iterates the full rewards array on every call, so every configured reward token is swept on every invocation even when the caller's claim touched none of them. Precondition: the configured convertor is SmartWomConvert and _minRec is set to zero.
- Invariant to test: settlement must be scoped to the reward tokens the caller's claim actually produced; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the configured convertor is SmartWomConvert and _minRec is set to zero, then assert `_convertRatio supplied by the caller` and `the value being converted for other users` end identical in both runs.
