# Q1044: IsERC20Registered token-pair collision

## Question
Can an unprivileged attacker enter through call a public ERC20/werc20 precompile method or submit `MsgConvertCoin` / `MsgConvertERC20` and use token amount, denom/contract address, receiver, sender, allowance state, callback timing, and calldata so that `x/erc20/keeper/token_pairs.go:IsERC20Registered` mishandles registration and routing because `IsERC20Registered` may normalize denom, prefix, or contract identity in a way that makes distinct assets share one token-pair slot or lets one asset resolve as another, causing `the canonical token-pair identity` and `the asset that user-controlled input resolves to` to diverge or settle in the wrong order, breaking the invariant that asset identity normalization must never alias two independently backed assets into the same accounting path and leading to `Supply inflation / accounting corruption`?

## Target
- File/function: `x/erc20/keeper/token_pairs.go:IsERC20Registered`
- Entrypoint: call a public ERC20/werc20 precompile method or submit `MsgConvertCoin` / `MsgConvertERC20`
- Attacker controls: token amount, denom/contract address, receiver, sender, allowance state, callback timing, and calldata
- Exploit idea: Drive the ERC20 / token-pair conversion path through a crafted path that reaches `IsERC20Registered` with attacker-controlled token amount, denom/contract address, receiver, sender, allowance state, callback timing, and calldata. Then force the failure, replay, nested-call, or ordering condition described above and compare `the canonical token-pair identity` against `the asset that user-controlled input resolves to`.
- Invariant to test: asset identity normalization must never alias two independently backed assets into the same accounting path
- Expected Immunefi impact: `Supply inflation / accounting corruption`
- Fast validation: fuzz denom prefixes, contract/denom encodings, and pair registration order and assert every asset resolves to exactly one unique backing record
