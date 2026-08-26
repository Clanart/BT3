# Q0417: ManualCompound.compound - compoundableRewards flag is the only filter on the first loop

## Question
In rewards/ManualCompound.sol, the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under a previous compound left a residue of one of the configured reward tokens on the contract, so that `_convertRatio supplied by the caller` diverges from `the value being converted for other users`, the invariant that an unregistered token must be rejected rather than treated as freely transferable is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: compoundableRewards flag is the only filter on the first loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Precondition: a previous compound left a residue of one of the configured reward tokens on the contract.
- Invariant to test: an unregistered token must be rejected rather than treated as freely transferable; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`: constrain the setup so that a previous compound left a residue of one of the configured reward tokens on the contract, fuzz the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls), and assert after every call that an unregistered token must be rejected rather than treated as freely transferable.
