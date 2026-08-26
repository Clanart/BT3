# Q2575: ManualCompound.compound - no reentrancy guard on a function that sweeps balances

## Question
rewards/ManualCompound.sol: compound() carries no nonReentrant while performing external claims, external converts and external transfers around balance reads, so a token with a transfer hook re-enters between the balance read and the settlement. With every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls under attacker control and the configured convertor is SmartWomConvert and _minRec is set to zero, can an unprivileged caller sequence `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` so that `compoundableRewards[token]` and `rewards[i].tokenAddress` no longer reconcile, violating the invariant that a function that settles from live balance reads must hold a reentrancy guard and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: no reentrancy guard on a function that sweeps balances)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: compound() carries no nonReentrant while performing external claims, external converts and external transfers around balance reads, so a token with a transfer hook re-enters between the balance read and the settlement. Precondition: the configured convertor is SmartWomConvert and _minRec is set to zero.
- Invariant to test: a function that settles from live balance reads must hold a reentrancy guard; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the configured convertor is SmartWomConvert and _minRec is set to zero, call `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, and assert `compoundableRewards[token]` equals `rewards[i].tokenAddress` and that no account can withdraw more than it put in.
