# Q382: DeleteNativePrecompile dynamic precompile confusion

## Question
Can an unprivileged attacker enter through call a public ERC20/werc20 precompile method or submit `MsgConvertCoin` / `MsgConvertERC20` and use precompile address selection; token amount, denom/contract address, receiver, sender, allowance state, callback timing, and calldata so that `x/erc20/keeper/precompiles.go:DeleteNativePrecompile` mishandles deletion / cleanup logic because `DeleteNativePrecompile` can register, resolve, or enable a dynamic/native precompile in a way that lets user-controlled assets hit the wrong code path or bypass expected restrictions, causing `the intended contract/precompile binding` and `the code path actually used for user-controlled assets` to diverge or settle in the wrong order, breaking the invariant that asset operations must resolve to exactly the precompile/contract implementation they are bound to and leading to `Privilege escalation / authorization bypass / unauthorized state mutation`?

## Target
- File/function: `x/erc20/keeper/precompiles.go:DeleteNativePrecompile`
- Entrypoint: call a public ERC20/werc20 precompile method or submit `MsgConvertCoin` / `MsgConvertERC20`
- Attacker controls: precompile address selection; token amount, denom/contract address, receiver, sender, allowance state, callback timing, and calldata
- Exploit idea: Drive the ERC20 / token-pair conversion path through a crafted path that reaches `DeleteNativePrecompile` with attacker-controlled precompile address selection; token amount, denom/contract address, receiver, sender, allowance state, callback timing, and calldata. Then force the failure, replay, nested-call, or ordering condition described above and compare `the intended contract/precompile binding` against `the code path actually used for user-controlled assets`.
- Invariant to test: asset operations must resolve to exactly the precompile/contract implementation they are bound to
- Expected Immunefi impact: `Privilege escalation / authorization bypass / unauthorized state mutation`
- Fast validation: exercise precompile registration and resolution with edge-case addresses and token pairs and assert asset operations cannot dispatch to the wrong implementation
