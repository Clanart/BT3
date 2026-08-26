# Q2697: ManualCompound.compound - stranded dust from a previous compound is claimable by anyone

## Question
In rewards/ManualCompound.sol, any token that a previous compound left behind through rounding, a partial convert or an under-consuming helper stays on the contract until the next caller sweeps the full balance. Starting from a state where _lockMgp is true and a locker is configured for the MGP entry, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `IERC20(_rewards[i][j]).balanceOf(address(this))` inconsistent with `the amount this caller actually claimed through multiclaimOnBehalf`, violating the invariant that residual value must be attributed to its owner rather than left claimable by the next caller and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: stranded dust from a previous compound is claimable by anyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: any token that a previous compound left behind through rounding, a partial convert or an under-consuming helper stays on the contract until the next caller sweeps the full balance. Precondition: _lockMgp is true and a locker is configured for the MGP entry.
- Invariant to test: residual value must be attributed to its owner rather than left claimable by the next caller; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence atomically under _lockMgp is true and a locker is configured for the MGP entry, asserting at the end that `IERC20(_rewards[i][j]).balanceOf(address(this))` still equals `the amount this caller actually claimed through multiclaimOnBehalf` and the PoC's balance delta is non-positive.
