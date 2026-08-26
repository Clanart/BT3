# Q0386: ManualCompound.compound - rewards array iterated for every caller regardless of what they claimed

## Question
In rewards/ManualCompound.sol, the second loop iterates the full rewards array on every call, so every configured reward token is swept on every invocation even when the caller's claim touched none of them. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under a previous compound left a residue of one of the configured reward tokens on the contract, so that `compoundableRewards[token]` diverges from `rewards[i].tokenAddress`, the invariant that settlement must be scoped to the reward tokens the caller's claim actually produced is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: rewards array iterated for every caller regardless of what they claimed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the second loop iterates the full rewards array on every call, so every configured reward token is swept on every invocation even when the caller's claim touched none of them. Precondition: a previous compound left a residue of one of the configured reward tokens on the contract.
- Invariant to test: settlement must be scoped to the reward tokens the caller's claim actually produced; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish a previous compound left a residue of one of the configured reward tokens on the contract, have the attacker run `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, then assert the victim's claimable value and the `compoundableRewards[token]` versus `rewards[i].tokenAddress` relation are unchanged by the attacker's transaction.
