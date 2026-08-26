# Q2834: ManualCompound.compound - safeApprove without reset on the convertor, locker and helper legs

## Question
In rewards/ManualCompound.sol, each branch calls safeApprove(target, receivedBalance) with no prior zeroing, so a single under-consuming target permanently disables compounding for every user. Starting from a state where _lockMgp is true and a locker is configured for the MGP entry, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `compoundableRewards[token]` inconsistent with `rewards[i].tokenAddress`, violating the invariant that approvals on a repeated settlement path must be idempotent and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: safeApprove without reset on the convertor, locker and helper legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: each branch calls safeApprove(target, receivedBalance) with no prior zeroing, so a single under-consuming target permanently disables compounding for every user. Precondition: _lockMgp is true and a locker is configured for the MGP entry.
- Invariant to test: approvals on a repeated settlement path must be idempotent; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange _lockMgp is true and a locker is configured for the MGP entry, call `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, and assert `compoundableRewards[token]` equals `rewards[i].tokenAddress` and that no account can withdraw more than it put in.
