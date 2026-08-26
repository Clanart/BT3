# Q1569: ManualCompound.compound - safeApprove without reset on the convertor, locker and helper legs

## Question
Consider rewards/ManualCompound.sol, where each branch calls safeApprove(target, receivedBalance) with no prior zeroing, so a single under-consuming target permanently disables compounding for every user. Assuming the caller passes an _rewards inner array naming a token that is not in the rewards registry, can an unprivileged attacker turn this into a divergence between `_convertRatio supplied by the caller` and `the value being converted for other users` via `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, breaking the invariant that approvals on a repeated settlement path must be idempotent and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: safeApprove without reset on the convertor, locker and helper legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: each branch calls safeApprove(target, receivedBalance) with no prior zeroing, so a single under-consuming target permanently disables compounding for every user. Precondition: the caller passes an _rewards inner array naming a token that is not in the rewards registry.
- Invariant to test: approvals on a repeated settlement path must be idempotent; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under the caller passes an _rewards inner array naming a token that is not in the rewards registry, asserting on every row that approvals on a repeated settlement path must be idempotent.
