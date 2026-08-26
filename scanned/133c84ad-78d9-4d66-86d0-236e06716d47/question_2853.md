# Q2853: ManualCompound.compound - no reentrancy guard on a function that sweeps balances

## Question
rewards/ManualCompound.sol: compound() carries no nonReentrant while performing external claims, external converts and external transfers around balance reads, so a token with a transfer hook re-enters between the balance read and the settlement. Under _lockMgp is true and a locker is configured for the MGP entry, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `_convertRatio supplied by the caller` unreconciled with `the value being converted for other users`, violates the invariant that a function that settles from live balance reads must hold a reentrancy guard, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: no reentrancy guard on a function that sweeps balances)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: compound() carries no nonReentrant while performing external claims, external converts and external transfers around balance reads, so a token with a transfer hook re-enters between the balance read and the settlement. Precondition: _lockMgp is true and a locker is configured for the MGP entry.
- Invariant to test: a function that settles from live balance reads must hold a reentrancy guard; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up _lockMgp is true and a locker is configured for the MGP entry, snapshot `_convertRatio supplied by the caller` and `the value being converted for other users`, run the attacker's `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
