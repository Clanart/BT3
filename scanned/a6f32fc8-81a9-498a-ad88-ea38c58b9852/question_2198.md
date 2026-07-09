# Q2198: EVM ENearProxy burn legacy withdrawal shortcut aliases a normal transfer via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` and then replay or reorder the later settlement leg on another chain so that `evm/src/eNear/contracts/ENearProxy.sol::burn` ends up accepting two inconsistent interpretations of the same economic event specifically around `legacy withdrawal shortcut aliases a normal transfer` under calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token, violating `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Target memo-based or self-transfer-based legacy shortcuts in token wrappers. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise equivalent economic transfers with and without legacy markers and assert that only the intended path creates a bridge event. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
