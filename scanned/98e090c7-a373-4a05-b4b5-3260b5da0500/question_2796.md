# Q2796: ManualCompound.compound - helper branch deposits the whole balance for msg.sender

## Question
In rewards/ManualCompound.sol, the ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender) branch credits the full contract balance of that token to the caller in MasterMagpie. Starting from a state where _lockMgp is true and a locker is configured for the MGP entry, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `IERC20(_rewards[i][j]).balanceOf(address(this))` inconsistent with `the amount this caller actually claimed through multiclaimOnBehalf`, violating the invariant that a deposit branch must credit only the caller's own earned amount and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: helper branch deposits the whole balance for msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender) branch credits the full contract balance of that token to the caller in MasterMagpie. Precondition: _lockMgp is true and a locker is configured for the MGP entry.
- Invariant to test: a deposit branch must credit only the caller's own earned amount; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under _lockMgp is true and a locker is configured for the MGP entry, then assert `IERC20(_rewards[i][j]).balanceOf(address(this))` and `the amount this caller actually claimed through multiclaimOnBehalf` end identical in both runs.
