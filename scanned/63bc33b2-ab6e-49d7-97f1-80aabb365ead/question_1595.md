# Q1595: ManualCompound.compound - no reentrancy guard on a function that sweeps balances

## Question
In rewards/ManualCompound.sol, compound() carries no nonReentrant while performing external claims, external converts and external transfers around balance reads, so a token with a transfer hook re-enters between the balance read and the settlement. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under the caller passes an _rewards inner array naming a token that is not in the rewards registry, so that `_minRec supplied by the caller` diverges from `obtainedmWomAmount in SmartWomConvert`, the invariant that a function that settles from live balance reads must hold a reentrancy guard is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: no reentrancy guard on a function that sweeps balances)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: compound() carries no nonReentrant while performing external claims, external converts and external transfers around balance reads, so a token with a transfer hook re-enters between the balance read and the settlement. Precondition: the caller passes an _rewards inner array naming a token that is not in the rewards registry.
- Invariant to test: a function that settles from live balance reads must hold a reentrancy guard; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller passes an _rewards inner array naming a token that is not in the rewards registry, have the attacker run `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, then assert the victim's claimable value and the `_minRec supplied by the caller` versus `obtainedmWomAmount in SmartWomConvert` relation are unchanged by the attacker's transaction.
