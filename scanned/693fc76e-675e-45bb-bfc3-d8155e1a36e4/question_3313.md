# Q3313: ManualCompound.compound - fallback branch transfers the whole balance to msg.sender

## Question
rewards/ManualCompound.sol: when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. With every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls under attacker control and the reward token has a transfer hook the caller controls, can an unprivileged caller sequence `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` so that `_convertRatio supplied by the caller` and `the value being converted for other users` no longer reconcile, violating the invariant that a fallback settlement branch must be bounded by the caller's own entitlement and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: fallback branch transfers the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Precondition: the reward token has a transfer hook the caller controls.
- Invariant to test: a fallback settlement branch must be bounded by the caller's own entitlement; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the value being converted for other users`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the reward token has a transfer hook the caller controls, have the attacker run `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, then assert the victim's claimable value and the `_convertRatio supplied by the caller` versus `the value being converted for other users` relation are unchanged by the attacker's transaction.
