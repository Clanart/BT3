### Title
`uint64` Underflow in `nestedCreates` Nonce Reconciliation Resets Sender Nonce After Failed Contract Creation — (`x/evm/keeper/state_transition.go`)

### Summary

In `ApplyMessageWithConfig`, the contract-creation branch resets the sender's nonce to `msg.Nonce` before calling `evm.Create()`, then reconciles the post-execution nonce with:

```go
nestedCreates := afterCreateNonce - msg.Nonce - 1
stateDB.SetNonce(sender, oldNonce+nestedCreates, ...)
```

When `evm.Create()` returns early **without incrementing the nonce** (e.g., `CanTransfer` fails because the sender's balance after fee deduction is less than `msg.Value`), `afterCreateNonce == msg.Nonce`, and the subtraction `afterCreateNonce - msg.Nonce - 1` silently wraps to `math.MaxUint64` in Go's unsigned arithmetic. The subsequent `oldNonce + math.MaxUint64` also wraps, setting the nonce back to `msg.Nonce` — the value **before** the ante handler's pre-increment — effectively undoing the nonce increment for a committed, fee-paying transaction.

### Finding Description

**Root cause — `x/evm/keeper/state_transition.go` lines 468–482:** [1](#0-0) 

```go
if contractCreation {
    oldNonce := stateDB.GetNonce(sender)          // = msg.Nonce + 1 (ante pre-incremented)
    stateDB.SetNonce(sender, msg.Nonce, ...)       // reset to msg.Nonce
    ret, _, leftoverGas, vmErr = evm.Create(...)
    // If evm.Create() returns early (CanTransfer fails), nonce is NOT incremented
    afterCreateNonce := stateDB.GetNonce(sender)

### Citations

**File:** x/evm/keeper/state_transition.go (L468-482)
```go
	if contractCreation {
		oldNonce := stateDB.GetNonce(sender)
		// take over the nonce management from evm:
		// - reset sender's nonce to msg.Nonce() before calling evm.
		// nonce is preincremented in antehandler, so we need to reset it here.
		// this is to ensure the nonce is correct for the creation of the contract.
		stateDB.SetNonce(sender, msg.Nonce, tracing.NonceChangeUnspecified)
		ret, _, leftoverGas, vmErr = evm.Create(sender, msg.Data, leftoverGas, uint256.MustFromBig(msg.Value))
		// evm.Create() increments nonce from msg.Nonce to (msg.Nonce + 1 + nestedCreates)
		// We need: oldNonce + nestedCreates
		afterCreateNonce := stateDB.GetNonce(sender)
		nestedCreates := afterCreateNonce - msg.Nonce - 1
		// setting nonce to the updated value is essential
		// as there may be subsequent evm call messages which doesn't increase nonce
		stateDB.SetNonce(sender, oldNonce+nestedCreates, tracing.NonceChangeUnspecified)
```
