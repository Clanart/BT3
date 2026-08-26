# Q2483: ManualCompound.compound - locker branch sends the whole balance to msg.sender

## Question
rewards/ManualCompound.sol: when _locker is configured and _lockMgp is true, ILocker(_locker).lockFor(receivedBalance, msg.sender) locks the contract's entire balance of that token for the caller. With every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls under attacker control and the configured convertor is SmartWomConvert and _minRec is set to zero, can an unprivileged caller sequence `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` so that `_convertRatio supplied by the caller` and `the value being converted for other users` no longer reconcile, violating the invariant that a locking branch must lock only the caller's own earned amount and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: locker branch sends the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when _locker is configured and _lockMgp is true, ILocker(_locker).lockFor(receivedBalance, msg.sender) locks the contract's entire balance of that token for the caller. Precondition: the configured convertor is SmartWomConvert and _minRec is set to zero.
- Invariant to test: a locking branch must lock only the caller's own earned amount; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the configured convertor is SmartWomConvert and _minRec is set to zero, then assert `_convertRatio supplied by the caller` and `the value being converted for other users` end identical in both runs.
