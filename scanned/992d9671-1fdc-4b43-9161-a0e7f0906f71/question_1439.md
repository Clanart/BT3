# Q1439: ManualCompound.compound - caller sets the slippage floor for value that is not theirs

## Question
In rewards/ManualCompound.sol, the caller-supplied _minRec is the only slippage protection applied to the convertFor leg, and it is applied to the entire contract balance rather than to the caller's own share. Starting from a state where the caller passes an _rewards inner array naming a token that is not in the rewards registry, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `_convertRatio supplied by the caller` inconsistent with `the value being converted for other users`, violating the invariant that the slippage floor on a shared-balance swap must be derived from protocol state and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: caller sets the slippage floor for value that is not theirs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the caller-supplied _minRec is the only slippage protection applied to the convertFor leg, and it is applied to the entire contract balance rather than to the caller's own share. Precondition: the caller passes an _rewards inner array naming a token that is not in the rewards registry.
- Invariant to test: the slippage floor on a shared-balance swap must be derived from protocol state; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence atomically under the caller passes an _rewards inner array naming a token that is not in the rewards registry, asserting at the end that `_convertRatio supplied by the caller` still equals `the value being converted for other users` and the PoC's balance delta is non-positive.
