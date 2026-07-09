# Q2502: EVM ENearProxy burn legacy withdrawal shortcut aliases a normal transfer at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/eNear/contracts/ENearProxy.sol::burn` violate `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once` in the `legacy withdrawal shortcut aliases a normal transfer` attack class because calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Target memo-based or self-transfer-based legacy shortcuts in token wrappers. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise equivalent economic transfers with and without legacy markers and assert that only the intended path creates a bridge event. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
