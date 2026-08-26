# Q0076: ManualCompound.compound - empty claim still triggers the sweep

## Question
In rewards/ManualCompound.sol, nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under a previous compound left a residue of one of the configured reward tokens on the contract, so that `compoundableRewards[token]` diverges from `rewards[i].tokenAddress`, the invariant that a distribution loop must be gated on the value the caller actually generated in the same call is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: empty claim still triggers the sweep)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: nothing in compound() requires the multiclaimOnBehalf leg to have produced any value, so a caller can pass staking tokens where they hold no stake and still reach both sweep loops. Precondition: a previous compound left a residue of one of the configured reward tokens on the contract.
- Invariant to test: a distribution loop must be gated on the value the caller actually generated in the same call; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a previous compound left a residue of one of the configured reward tokens on the contract, snapshot `compoundableRewards[token]` and `rewards[i].tokenAddress`, run the attacker's `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
