# Q421: Token Price Undercharge Across Mixed Context

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and mixing bytes that were valid in one proof, chain, module, order, beneficiary, or signature context with metadata interpreted in another context, and make `_fetchDetails` derive a cheaper token charge than the operation's true native gas cost through decimal, oracle, or markup normalization errors so `the token-denominated gas charge` becomes inconsistent with `the native gas cost converted under the intended oracle and markup model`, breaking the invariant that gas sponsorship must never undercharge relative to configured oracle prices, decimals, markup, and postOp cost assumptions and leading to Critical: the paymaster subsidizes more gas than the charged token amount covers?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::_fetchDetails
- Entrypoint: EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Derive a cheaper token charge than the operation's true native gas cost through decimal, oracle, or markup normalization errors. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: gas sponsorship must never undercharge relative to configured oracle prices, decimals, markup, and postOp cost assumptions
- Expected Immunefi impact: Critical: the paymaster subsidizes more gas than the charged token amount covers.
- Fast validation: Vary oracle decimals, token decimals, and edge gas values and assert the computed token charge is never lower than the intended economic minimum. Build two neighboring valid contexts and mutate only the binding field while asserting validation, state, and balances stay unchanged.
