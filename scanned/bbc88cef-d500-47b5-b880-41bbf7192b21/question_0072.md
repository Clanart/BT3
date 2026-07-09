# Q72: EVM ENearProxy mint replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` and make `evm/src/eNear/contracts/ENearProxy.sol::mint` either bypass replay protection or consume it for the wrong event because of fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR, violating `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
