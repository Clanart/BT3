# Q3075: ManualCompound.compound - fallback branch transfers the whole balance to msg.sender

## Question
In rewards/ManualCompound.sol, when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Starting from a state where no convertor, locker or helper is configured for one of the registered rewards, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `compoundableRewards[token]` inconsistent with `rewards[i].tokenAddress`, violating the invariant that a fallback settlement branch must be bounded by the caller's own entitlement and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: fallback branch transfers the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Precondition: no convertor, locker or helper is configured for one of the registered rewards.
- Invariant to test: a fallback settlement branch must be bounded by the caller's own entitlement; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence atomically under no convertor, locker or helper is configured for one of the registered rewards, asserting at the end that `compoundableRewards[token]` still equals `rewards[i].tokenAddress` and the PoC's balance delta is non-positive.
