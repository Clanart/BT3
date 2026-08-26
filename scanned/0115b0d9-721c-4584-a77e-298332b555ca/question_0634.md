# Q0634: ManualCompound.compound - converted output is directed to msg.sender

## Question
rewards/ManualCompound.sol: convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Under another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `compoundableRewards[token]` unreconciled with `rewards[i].tokenAddress`, violates the invariant that converted value must be attributed to the account that earned it, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: converted output is directed to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Precondition: another user's multiclaimOnBehalf is pending in the mempool and will land in the same block.
- Invariant to test: converted value must be attributed to the account that earned it; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, then assert `compoundableRewards[token]` and `rewards[i].tokenAddress` end identical in both runs.
