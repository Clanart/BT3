# Q145: ERC1271 Signature Confusion After Partial State Change

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SolverAccount.validateUserOp` with attacker-controlled user-operation calldata, signature bytes, nonce and session material, executionData, and missingAccountFunds and replaying the same public flow after one part of storage changed and another part did not, and make `isValidSignature` accept a signature under ERC1271 semantics that should fail under the raw solver-account authorization model so `the signature interpretation used by delegated-account integrations` becomes inconsistent with `the exact hash and signature that the solver account actually authorized`, breaking the invariant that ERC1271 checks must stay consistent with raw signature validation so integrations cannot accept a different authorization domain and leading to Critical: external integrations treat an unauthorized signature as valid and release value or approvals?

## Target
- File/function: evm/src/apps/intentsv2/SolverAccount.sol::isValidSignature
- Entrypoint: EntryPoint.handleOps(userOps) -> SolverAccount.validateUserOp
- Attacker controls: user-operation calldata, signature bytes, nonce and session material, executionData, and missingAccountFunds
- Exploit idea: Accept a signature under ERC1271 semantics that should fail under the raw solver-account authorization model. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: ERC1271 checks must stay consistent with raw signature validation so integrations cannot accept a different authorization domain
- Expected Immunefi impact: Critical: external integrations treat an unauthorized signature as valid and release value or approvals.
- Fast validation: Replay signatures across raw validation and ERC1271 checks and assert both paths agree on exactly the same accepted hashes. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, validation state, and balances stay coherent.
