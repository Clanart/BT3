# Q1566: EVM ENearProxy burn legacy or migration path aliasing via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` and then replay or reorder the later settlement leg on another chain so that `evm/src/eNear/contracts/ENearProxy.sol::burn` ends up accepting two inconsistent interpretations of the same economic event specifically around `legacy or migration path aliasing` under calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token, violating `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Use memo-triggered legacy paths, migrated-token aliases, or old/new token relationships to create a second valid outbound interpretation of the same balance change. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise both the modern and legacy branches with equivalent economic inputs and assert that only one bridge claim can arise from one unit of consumed value. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
