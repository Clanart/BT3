# Q0324: ManualCompound.compound - safeApprove without reset on the convertor, locker and helper legs

## Question
In rewards/ManualCompound.sol, each branch calls safeApprove(target, receivedBalance) with no prior zeroing, so a single under-consuming target permanently disables compounding for every user. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under a previous compound left a residue of one of the configured reward tokens on the contract, so that `IERC20(_rewards[i][j]).balanceOf(address(this))` diverges from `the amount this caller actually claimed through multiclaimOnBehalf`, the invariant that approvals on a repeated settlement path must be idempotent is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: safeApprove without reset on the convertor, locker and helper legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: each branch calls safeApprove(target, receivedBalance) with no prior zeroing, so a single under-consuming target permanently disables compounding for every user. Precondition: a previous compound left a residue of one of the configured reward tokens on the contract.
- Invariant to test: approvals on a repeated settlement path must be idempotent; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence atomically under a previous compound left a residue of one of the configured reward tokens on the contract, asserting at the end that `IERC20(_rewards[i][j]).balanceOf(address(this))` still equals `the amount this caller actually claimed through multiclaimOnBehalf` and the PoC's balance delta is non-positive.
