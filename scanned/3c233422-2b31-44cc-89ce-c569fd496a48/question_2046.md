# Q2046: ManualCompound.compound - empty claim still triggers the sweep

## Question
rewards/ManualCompound.sol: nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Under the configured convertor is SmartWomConvert and _convertRatio is set to zero, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `compoundableRewards[token]` unreconciled with `rewards[i].tokenAddress`, violates the invariant that a distribution loop must be gated on the value the caller actually generated in the same call, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: empty claim still triggers the sweep)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Precondition: the configured convertor is SmartWomConvert and _convertRatio is set to zero.
- Invariant to test: a distribution loop must be gated on the value the caller actually generated in the same call; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the configured convertor is SmartWomConvert and _convertRatio is set to zero, call `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, and assert `compoundableRewards[token]` equals `rewards[i].tokenAddress` and that no account can withdraw more than it put in.
