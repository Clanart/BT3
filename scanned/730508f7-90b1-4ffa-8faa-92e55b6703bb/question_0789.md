# Q0789: ManualCompound.compound - no reentrancy guard on a function that sweeps balances

## Question
rewards/ManualCompound.sol: compound() carries no nonReentrant while performing external claims, external converts and external transfers around balance reads, so a token with a transfer hook re-enters between the balance read and the settlement. Under another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `compoundableRewards[token]` unreconciled with `rewards[i].tokenAddress`, violates the invariant that a function that settles from live balance reads must hold a reentrancy guard, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: no reentrancy guard on a function that sweeps balances)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: compound() carries no nonReentrant while performing external claims, external converts and external transfers around balance reads, so a token with a transfer hook re-enters between the balance read and the settlement. Precondition: another user's multiclaimOnBehalf is pending in the mempool and will land in the same block.
- Invariant to test: a function that settles from live balance reads must hold a reentrancy guard; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`: constrain the setup so that another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, fuzz the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls), and assert after every call that a function that settles from live balance reads must hold a reentrancy guard.
