# Q2161: ManualCompound.compound - locker branch sends the whole balance to msg.sender

## Question
rewards/ManualCompound.sol: when _locker is configured and _lockMgp is true, ILocker(_locker).lockFor(receivedBalance, msg.sender) locks the contract's entire balance of that token for the caller. Under the configured convertor is SmartWomConvert and _convertRatio is set to zero, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `compoundableRewards[token]` unreconciled with `rewards[i].tokenAddress`, violates the invariant that a locking branch must lock only the caller's own earned amount, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: locker branch sends the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when _locker is configured and _lockMgp is true, ILocker(_locker).lockFor(receivedBalance, msg.sender) locks the contract's entire balance of that token for the caller. Precondition: the configured convertor is SmartWomConvert and _convertRatio is set to zero.
- Invariant to test: a locking branch must lock only the caller's own earned amount; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`: constrain the setup so that the configured convertor is SmartWomConvert and _convertRatio is set to zero, fuzz the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls), and assert after every call that a locking branch must lock only the caller's own earned amount.
