# Q1888: EVM ENearProxy burn legacy or migration path aliasing at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/eNear/contracts/ENearProxy.sol::burn` violate `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once` in the `legacy or migration path aliasing` attack class because calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Use memo-triggered legacy paths, migrated-token aliases, or old/new token relationships to create a second valid outbound interpretation of the same balance change. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise both the modern and legacy branches with equivalent economic inputs and assert that only one bridge claim can arise from one unit of consumed value. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
