# Q2207: ManualCompound.compound - fallback branch transfers the whole balance to msg.sender

## Question
rewards/ManualCompound.sol: when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Under the configured convertor is SmartWomConvert and _convertRatio is set to zero, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `_minRec supplied by the caller` unreconciled with `obtainedmWomAmount in SmartWomConvert`, violates the invariant that a fallback settlement branch must be bounded by the caller's own entitlement, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: fallback branch transfers the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Precondition: the configured convertor is SmartWomConvert and _convertRatio is set to zero.
- Invariant to test: a fallback settlement branch must be bounded by the caller's own entitlement; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the configured convertor is SmartWomConvert and _convertRatio is set to zero, call `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, and assert `_minRec supplied by the caller` equals `obtainedmWomAmount in SmartWomConvert` and that no account can withdraw more than it put in.
