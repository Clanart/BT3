# Q3799: NEAR EVM eNear interface path legacy withdrawal shortcut aliases a normal transfer

## Question
Can an unprivileged attacker use `legacy/public eNEAR mint/burn/finalize flows` to turn a normal token transfer into a bridge withdrawal or vice versa because `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` keys off legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state, violating `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions`?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Target memo-based or self-transfer-based legacy shortcuts in token wrappers.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise equivalent economic transfers with and without legacy markers and assert that only the intended path creates a bridge event.
