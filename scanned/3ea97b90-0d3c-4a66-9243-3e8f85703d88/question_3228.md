# Q3228: ManualCompound.compound - caller sets the conversion ratio for value that is not theirs

## Question
rewards/ManualCompound.sol: compound() forwards the caller's _convertRatio into IConverter(_convertor).convertFor(receivedBalance, _convertRatio, _minRec, msg.sender, 2) where receivedBalance is the whole contract balance, so one caller decides how another user's value is routed through the AMM. Under the reward token has a transfer hook the caller controls, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `_convertRatio supplied by the caller` unreconciled with `the value being converted for other users`, violates the invariant that a routing parameter that decides how shared value is traded must not be caller-supplied, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: caller sets the conversion ratio for value that is not theirs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: compound() forwards the caller's _convertRatio into IConverter(_convertor).convertFor(receivedBalance, _convertRatio, _minRec, msg.sender, 2) where receivedBalance is the whole contract balance, so one caller decides how another user's value is routed through the AMM. Precondition: the reward token has a transfer hook the caller controls.
- Invariant to test: a routing parameter that decides how shared value is traded must not be caller-supplied; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under the reward token has a transfer hook the caller controls, asserting on every row that a routing parameter that decides how shared value is traded must not be caller-supplied.
