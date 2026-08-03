# Q143: ERC1271 Signature Confusion Across Mixed Context

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SolverAccount.validateUserOp` with attacker-controlled user-operation calldata, signature bytes, nonce and session material, executionData, and missingAccountFunds and mixing bytes that were valid in one proof, chain, module, order, beneficiary, or signature context with metadata interpreted in another context, and make `isValidSignature` accept a signature under ERC1271 semantics that should fail under the raw solver-account authorization model so `the signature interpretation used by delegated-account integrations` becomes inconsistent with `the exact hash and signature that the solver account actually authorized`, breaking the invariant that ERC1271 checks must stay consistent with raw signature validation so integrations cannot accept a different authorization domain and leading to Critical: external integrations treat an unauthorized signature as valid and release value or approvals?

## Target
- File/function: evm/src/apps/intentsv2/SolverAccount.sol::isValidSignature
- Entrypoint: EntryPoint.handleOps(userOps) -> SolverAccount.validateUserOp
- Attacker controls: user-operation calldata, signature bytes, nonce and session material, executionData, and missingAccountFunds
- Exploit idea: Accept a signature under ERC1271 semantics that should fail under the raw solver-account authorization model. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: ERC1271 checks must stay consistent with raw signature validation so integrations cannot accept a different authorization domain
- Expected Immunefi impact: Critical: external integrations treat an unauthorized signature as valid and release value or approvals.
- Fast validation: Replay signatures across raw validation and ERC1271 checks and assert both paths agree on exactly the same accepted hashes. Build two neighboring valid contexts and mutate only the binding field while asserting validation, state, and balances stay unchanged.
