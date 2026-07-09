# Q1405: EVM ENearProxy burn legacy or migration path aliasing

## Question
Can an unprivileged attacker route the same value through both the normal and legacy/migration semantics behind `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` because `evm/src/eNear/contracts/ENearProxy.sol::burn` depends on calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token, violating `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Use memo-triggered legacy paths, migrated-token aliases, or old/new token relationships to create a second valid outbound interpretation of the same balance change.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise both the modern and legacy branches with equivalent economic inputs and assert that only one bridge claim can arise from one unit of consumed value.
