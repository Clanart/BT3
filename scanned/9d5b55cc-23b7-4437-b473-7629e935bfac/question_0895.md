# Q895: CreateNewTokenPair pair liveness freeze

## Question
Can an unprivileged attacker enter through call a public ERC20/werc20 precompile method or submit `MsgConvertCoin` / `MsgConvertERC20` and use denom normalization and prefix choices; token amount, denom/contract address, receiver, sender, allowance state, callback timing, and calldata so that `x/erc20/keeper/token_pairs.go:CreateNewTokenPair` mishandles ERC20 / token-pair conversion path because `CreateNewTokenPair` can hit a self-destructed, missing, or stale contract/pair path where one side of the representation is deleted or disabled without safely unwinding the backing state, causing `the token-pair liveness metadata` and `the backing balances still tied to that pair` to diverge or settle in the wrong order, breaking the invariant that pair disablement or contract death must not permanently strand balances or prevent a valid redemption path and leading to `Permanent locking / freezing of funds or clients`?

## Target
- File/function: `x/erc20/keeper/token_pairs.go:CreateNewTokenPair`
- Entrypoint: call a public ERC20/werc20 precompile method or submit `MsgConvertCoin` / `MsgConvertERC20`
- Attacker controls: denom normalization and prefix choices; token amount, denom/contract address, receiver, sender, allowance state, callback timing, and calldata
- Exploit idea: Drive the ERC20 / token-pair conversion path through a crafted path that reaches `CreateNewTokenPair` with attacker-controlled denom normalization and prefix choices; token amount, denom/contract address, receiver, sender, allowance state, callback timing, and calldata. Then force the failure, replay, nested-call, or ordering condition described above and compare `the token-pair liveness metadata` against `the backing balances still tied to that pair`.
- Invariant to test: pair disablement or contract death must not permanently strand balances or prevent a valid redemption path
- Expected Immunefi impact: `Permanent locking / freezing of funds or clients`
- Fast validation: simulate contract death or stale pair metadata before conversion or transfer and assert balances remain recoverable through a safe path
