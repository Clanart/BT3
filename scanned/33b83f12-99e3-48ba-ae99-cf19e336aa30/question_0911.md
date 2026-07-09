# Q911: EVM ENearProxy finaliseNearToEthTransfer state update before full validation via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public legacy proof-submission entrypoint` and then replay or reorder another proof-consuming public entrypoint so that `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `state update before full validation` under delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer`, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
