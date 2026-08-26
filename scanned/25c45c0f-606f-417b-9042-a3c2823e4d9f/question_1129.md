# Q1129: ManualCompound.compound - helper branch deposits the whole balance for msg.sender

## Question
In rewards/ManualCompound.sol, the ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender) branch credits the full contract balance of that token to the caller in MasterMagpie. Can an unprivileged attacker reach this through `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` while the caller passes an _lps array of pools where they hold no stake at all, and drive `IERC20(_rewards[i][j]).balanceOf(address(this))` out of agreement with `the amount this caller actually claimed through multiclaimOnBehalf` - breaking the invariant that a deposit branch must credit only the caller's own earned amount - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: helper branch deposits the whole balance for msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender) branch credits the full contract balance of that token to the caller in MasterMagpie. Precondition: the caller passes an _lps array of pools where they hold no stake at all.
- Invariant to test: a deposit branch must credit only the caller's own earned amount; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence atomically under the caller passes an _lps array of pools where they hold no stake at all, asserting at the end that `IERC20(_rewards[i][j]).balanceOf(address(this))` still equals `the amount this caller actually claimed through multiclaimOnBehalf` and the PoC's balance delta is non-positive.
