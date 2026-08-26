# Q3330: ManualCompound.compound - safeApprove without reset on the convertor, locker and helper legs

## Question
In rewards/ManualCompound.sol, each branch calls safeApprove(target, receivedBalance) with no prior zeroing, so a single under-consuming target permanently disables compounding for every user. Starting from a state where the reward token has a transfer hook the caller controls, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `_minRec supplied by the caller` inconsistent with `obtainedmWomAmount in SmartWomConvert`, violating the invariant that approvals on a repeated settlement path must be idempotent and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: safeApprove without reset on the convertor, locker and helper legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: each branch calls safeApprove(target, receivedBalance) with no prior zeroing, so a single under-consuming target permanently disables compounding for every user. Precondition: the reward token has a transfer hook the caller controls.
- Invariant to test: approvals on a repeated settlement path must be idempotent; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`: constrain the setup so that the reward token has a transfer hook the caller controls, fuzz the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls), and assert after every call that approvals on a repeated settlement path must be idempotent.
