# Q2677: ManualCompound.compound - empty claim still triggers the sweep

## Question
Note that in rewards/ManualCompound.sol, nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Can an attacker holding only tokens bought on market reach it via `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` under _lockMgp is true and a locker is configured for the MGP entry and force `_minRec supplied by the caller` apart from `obtainedmWomAmount in SmartWomConvert`, breaking the invariant that a distribution loop must be gated on the value the caller actually generated in the same call for Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: empty claim still triggers the sweep)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Precondition: _lockMgp is true and a locker is configured for the MGP entry.
- Invariant to test: a distribution loop must be gated on the value the caller actually generated in the same call; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence atomically under _lockMgp is true and a locker is configured for the MGP entry, asserting at the end that `_minRec supplied by the caller` still equals `obtainedmWomAmount in SmartWomConvert` and the PoC's balance delta is non-positive.
