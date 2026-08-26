# Q1037: ManualCompound.compound - caller sets the slippage floor for value that is not theirs

## Question
rewards/ManualCompound.sol: the caller-supplied _minRec is the only slippage protection applied to the convertFor leg, and it is applied to the entire contract balance rather than to the caller's own share. With every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls under attacker control and the caller passes an _lps array of pools where they hold no stake at all, can an unprivileged caller sequence `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` so that `compoundableRewards[token]` and `rewards[i].tokenAddress` no longer reconcile, violating the invariant that the slippage floor on a shared-balance swap must be derived from protocol state and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: caller sets the slippage floor for value that is not theirs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the caller-supplied _minRec is the only slippage protection applied to the convertFor leg, and it is applied to the entire contract balance rather than to the caller's own share. Precondition: the caller passes an _lps array of pools where they hold no stake at all.
- Invariant to test: the slippage floor on a shared-balance swap must be derived from protocol state; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the caller passes an _lps array of pools where they hold no stake at all, snapshot `compoundableRewards[token]` and `rewards[i].tokenAddress`, run the attacker's `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
