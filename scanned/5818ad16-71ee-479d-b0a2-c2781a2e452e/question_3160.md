# Q3160: ManualCompound.compound - full contract balance paid to the caller instead of the claimed delta

## Question
rewards/ManualCompound.sol: compound() settles every configured reward with receivedBalance = IERC20(_tokenAddress).balanceOf(address(this)) rather than the delta produced by this caller's multiclaimOnBehalf, so any balance already sitting on the contract is handed to whoever calls next. Under the reward token has a transfer hook the caller controls, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `_minRec supplied by the caller` unreconciled with `obtainedmWomAmount in SmartWomConvert`, violates the invariant that a compounding caller must only ever receive the value their own claim produced, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: full contract balance paid to the caller instead of the claimed delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: compound() settles every configured reward with receivedBalance = IERC20(_tokenAddress).balanceOf(address(this)) rather than the delta produced by this caller's multiclaimOnBehalf, so any balance already sitting on the contract is handed to whoever calls next. Precondition: the reward token has a transfer hook the caller controls.
- Invariant to test: a compounding caller must only ever receive the value their own claim produced; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence atomically under the reward token has a transfer hook the caller controls, asserting at the end that `_minRec supplied by the caller` still equals `obtainedmWomAmount in SmartWomConvert` and the PoC's balance delta is non-positive.
