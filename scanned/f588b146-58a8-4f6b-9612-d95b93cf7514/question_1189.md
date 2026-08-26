# Q1189: ManualCompound.compound - safeApprove without reset on the convertor, locker and helper legs

## Question
Note that in rewards/ManualCompound.sol, each branch calls safeApprove(target, receivedBalance) with no prior zeroing, so a single under-consuming target permanently disables compounding for every user. Can an attacker holding only tokens bought on market reach it via `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` under the caller passes an _lps array of pools where they hold no stake at all and force `compoundableRewards[token]` apart from `rewards[i].tokenAddress`, breaking the invariant that approvals on a repeated settlement path must be idempotent for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: safeApprove without reset on the convertor, locker and helper legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: each branch calls safeApprove(target, receivedBalance) with no prior zeroing, so a single under-consuming target permanently disables compounding for every user. Precondition: the caller passes an _lps array of pools where they hold no stake at all.
- Invariant to test: approvals on a repeated settlement path must be idempotent; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller passes an _lps array of pools where they hold no stake at all, then assert `compoundableRewards[token]` and `rewards[i].tokenAddress` end identical in both runs.
