# Q0231: ManualCompound.compound - locker branch sends the whole balance to msg.sender

## Question
In rewards/ManualCompound.sol, when _locker is configured and _lockMgp is true, ILocker(_locker).lockFor(receivedBalance, msg.sender) locks the contract's entire balance of that token for the caller. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under a previous compound left a residue of one of the configured reward tokens on the contract, so that `compoundableRewards[token]` diverges from `rewards[i].tokenAddress`, the invariant that a locking branch must lock only the caller's own earned amount is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: locker branch sends the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when _locker is configured and _lockMgp is true, ILocker(_locker).lockFor(receivedBalance, msg.sender) locks the contract's entire balance of that token for the caller. Precondition: a previous compound left a residue of one of the configured reward tokens on the contract.
- Invariant to test: a locking branch must lock only the caller's own earned amount; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under a previous compound left a residue of one of the configured reward tokens on the contract, asserting on every row that a locking branch must lock only the caller's own earned amount.
