# Q0510: ManualCompound.compound - empty claim still triggers the sweep

## Question
rewards/ManualCompound.sol: nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Under another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `_convertRatio supplied by the caller` unreconciled with `the value being converted for other users`, violates the invariant that a distribution loop must be gated on the value the caller actually generated in the same call, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: empty claim still triggers the sweep)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Precondition: another user's multiclaimOnBehalf is pending in the mempool and will land in the same block.
- Invariant to test: a distribution loop must be gated on the value the caller actually generated in the same call; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, call `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, and assert `_convertRatio supplied by the caller` equals `the value being converted for other users` and that no account can withdraw more than it put in.
