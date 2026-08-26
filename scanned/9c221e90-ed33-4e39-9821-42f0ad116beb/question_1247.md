# Q1247: ManualCompound.compound - rewards array iterated for every caller regardless of what they claimed

## Question
Consider rewards/ManualCompound.sol, where the second loop iterates the full rewards array on every call, so every configured reward token is swept on every invocation even when the caller's claim touched none of them. Assuming the caller passes an _lps array of pools where they hold no stake at all, can an unprivileged attacker turn this into a divergence between `_minRec supplied by the caller` and `obtainedmWomAmount in SmartWomConvert` via `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, breaking the invariant that settlement must be scoped to the reward tokens the caller's claim actually produced and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: rewards array iterated for every caller regardless of what they claimed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the second loop iterates the full rewards array on every call, so every configured reward token is swept on every invocation even when the caller's claim touched none of them. Precondition: the caller passes an _lps array of pools where they hold no stake at all.
- Invariant to test: settlement must be scoped to the reward tokens the caller's claim actually produced; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the caller passes an _lps array of pools where they hold no stake at all, snapshot `_minRec supplied by the caller` and `obtainedmWomAmount in SmartWomConvert`, run the attacker's `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
