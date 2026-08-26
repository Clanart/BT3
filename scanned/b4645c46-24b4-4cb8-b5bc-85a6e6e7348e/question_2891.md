# Q2891: ManualCompound.compound - compoundableRewards flag is the only filter on the first loop

## Question
rewards/ManualCompound.sol: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Under _lockMgp is true and a locker is configured for the MGP entry, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `IERC20(_rewards[i][j]).balanceOf(address(this))` unreconciled with `the amount this caller actually claimed through multiclaimOnBehalf`, violates the invariant that an unregistered token must be rejected rather than treated as freely transferable, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: compoundableRewards flag is the only filter on the first loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Precondition: _lockMgp is true and a locker is configured for the MGP entry.
- Invariant to test: an unregistered token must be rejected rather than treated as freely transferable; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish _lockMgp is true and a locker is configured for the MGP entry, have the attacker run `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, then assert the victim's claimable value and the `IERC20(_rewards[i][j]).balanceOf(address(this))` versus `the amount this caller actually claimed through multiclaimOnBehalf` relation are unchanged by the attacker's transaction.
