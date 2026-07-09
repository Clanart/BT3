# Q2046: EVM ENearProxy burn legacy withdrawal shortcut aliases a normal transfer

## Question
Can an unprivileged attacker use `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows` to turn a normal token transfer into a bridge withdrawal or vice versa because `evm/src/eNear/contracts/ENearProxy.sol::burn` keys off calls `eNear.transferToNear` after asserting the token address is the configured eNEAR token, violating `legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::burn`
- Entrypoint: `public bridge-side burn path behind `MINTER_ROLE` but used by outbound bridge flows`
- Attacker controls: token address and amount
- Exploit idea: Target memo-based or self-transfer-based legacy shortcuts in token wrappers.
- Invariant to test: legacy outbound burns must not let attackers create Near-side withdrawal claims without consuming the exact on-chain eNEAR balance once
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise equivalent economic transfers with and without legacy markers and assert that only the intended path creates a bridge event.
