# Q424: Token Price Undercharge By Reusing Data Cross Module

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and reusing data that should belong to one chain, module, account, or order in another publicly reachable path, and make `_fetchDetails` derive a cheaper token charge than the operation's true native gas cost through decimal, oracle, or markup normalization errors so `the token-denominated gas charge` becomes inconsistent with `the native gas cost converted under the intended oracle and markup model`, breaking the invariant that gas sponsorship must never undercharge relative to configured oracle prices, decimals, markup, and postOp cost assumptions and leading to Critical: the paymaster subsidizes more gas than the charged token amount covers?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::_fetchDetails
- Entrypoint: EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Derive a cheaper token charge than the operation's true native gas cost through decimal, oracle, or markup normalization errors. Craft two public flows that share one byte string or hash and check whether module, chain, or account binding is enforced everywhere.
- Invariant to test: gas sponsorship must never undercharge relative to configured oracle prices, decimals, markup, and postOp cost assumptions
- Expected Immunefi impact: Critical: the paymaster subsidizes more gas than the charged token amount covers.
- Fast validation: Vary oracle decimals, token decimals, and edge gas values and assert the computed token charge is never lower than the intended economic minimum. Feed the same bytes through two reachable entrypoints and assert the second path rejects instead of inheriting the first path's authorization.
