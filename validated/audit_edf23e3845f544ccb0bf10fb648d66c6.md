### Title
Nonce Underflow After Failed Contract Creation Enables Nonce Replay — (File: `x/evm/keeper/state_transition.go`)

---

### Summary

In `ApplyMessageWithConfig`, the nonce-restoration logic for contract creation transactions contains an unchecked uint64 underflow. When `evm.Create` fails (any `vmErr`), the EVM reverts its internal snapshot and leaves the sender nonce at `msg.Nonce`. The subsequent arithmetic then wraps around, committing the sender's nonce as `msg.Nonce` instead of the correct `msg.Nonce + 1`. This allows the sender to reuse the same nonce for a subsequent transaction, bypassing Ethereum's nonce-ordering guarantee.

---

### Finding Description

`ApplyMessageWithConfig` contains the following block for contract-creation messages:

```go
// x/evm/keeper/state_transition.go  lines 468-482
if contractCreation {
    oldNonce := stateDB.GetNonce(sender)          // = msg.Nonce + 1 (ante pre-incremented)
    stateDB.SetNonce(sender, msg.Nonce, ...)       // reset to msg.Nonce for EVM
    ret, _, leftoverGas, vmErr = evm.Create(...)
    // evm