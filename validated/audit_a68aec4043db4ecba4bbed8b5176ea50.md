### Title
`Simulator.applyCall` Unconditionally Resets Nonce to `msg.Nonce+1` After `evm.Call()`, Discarding Nested-CREATE Increments and Producing Wrong Contract Addresses in `eth_simulateV1` - (File: `x/evm/keeper/simulate.go`)

---

### Summary

In `Simulator.applyCall()`, after `evm.Call()` returns, the sender's nonce is unconditionally overwritten with `msg.Nonce+1`. When an EIP-7702 delegated CALL triggers nested `CREATE` opcodes during execution, those CREATEs increment the nonce beyond `msg.Nonce+1`. The hard-coded `SetNonce` call discards those increments. Every subsequent call in the same `eth_simulateV1` block then operates on a stale nonce, producing wrong contract addresses and wrong nonce-validation outcomes — a divergence from what actual on-chain execution would produce.

---

### Finding Description

**Root cause — `x/evm/keeper/simulate.go`, `applyCall()`:**

Before execution the nonce is set to the message's declared nonce:

```go
// line 432
sim.state.SetNonce(msg.From, msg.Nonce, tracing.NonceChangeUnspecified)
```

For the CALL branch, after `evm.Call()` returns, the nonce is unconditionally pinned to `msg.Nonce+1`:

```go
// lines 449-450
ret, leftoverGas, vmErr = evm.Call(msg.From, *msg.To, msg.Data, leftoverGas, value)
sim.state.SetNonce(msg.From, msg.Nonce+1, tracing.NonceChangeUnspecified)
``` [1](#0-0) 

`evm.Call()` itself does not increment the caller's nonce. However, if the callee is an EIP-7702 delegated account whose delegated code executes `CREATE` opcodes, each `CREATE` increments the sender's nonce inside the EVM. After `evm.Call()` returns the stateDB nonce is therefore `msg.Nonce + nested_creates`. The subsequent `SetNonce(msg.From, msg.Nonce+1, …)` resets it back to `msg.Nonce+1`, silently discarding `nested_creates - 1` increments.

**Contrast with the production execution path:**

`ApplyMessageWithConfig` in `state_transition.go` handles the CREATE branch with explicit reconciliation:

```go
// lines 469-482
oldNonce := stateDB.GetNonce(sender)
stateDB.SetNonce(sender, msg.Nonce, ...)
ret, _, leftoverGas, vmErr = evm.Create(...)
afterCreateNonce := stateDB.GetNonce(sender)
nestedCreates := afterCreateNonce - msg.Nonce - 1
stateDB.SetNonce(sender, oldNonce+nestedCreates, ...)
``` [2](#0-1) 

The CALL branch in the production path does **not** reset the nonce at all after `evm.Call()` — it relies on the ante handler's pre-increment and lets nested-CREATE increments accumulate naturally:

```go
// lines 515-517
// based on geth, nonce should be preincremented before evm call execution
// which is already done on the antehandler
ret, leftoverGas, vmErr = evm.Call(sender, *msg.To, ...)
``` [3](#0-2) 

The simulation path has no ante handler, so it manually manages the nonce — but it does so incorrectly for the CALL branch.

**EIP-7702 authorization nonce interaction:**

`applyAuthorization` / `setAuthorizationDelegation` also increments the authority's nonce:

```go
// line 76
stateDB.SetNonce(authority, auth.Nonce+1, tracing.NonceChangeAuthorization)
``` [4](#0-3) 

When the authority is the same as the sender (self-delegation), this increment is applied before `evm.Call()`. The subsequent `SetNonce(msg.From, msg.Nonce+1, …)` then also overwrites the authorization-nonce increment, compounding the error.

---

### Impact Explanation

This is a **High** impact finding under the category: *"Public JSON-RPC … simulation … path feeds incorrect consensus-critical data into transaction execution or exposes a reachable route to the impacts above."*

Concrete consequences for a multi-call `eth_simulateV1` request:

1. **Wrong contract addresses.** A subsequent call in the same simulated block that performs a `CREATE` will use the stale (too-low) nonce to derive the contract address. The predicted address diverges from what actual on-chain execution would produce.

2. **Wrong nonce validation.** With `sim.validate = true`, the next call's nonce check (`msg.Nonce < stateNonce` / `msg.Nonce > stateNonce`) operates on the wrong baseline, causing valid calls to be rejected or invalid calls to be accepted within the simulation.

3. **Fund-loss route.** Protocols that use `eth_simulateV1` to pre-compute deployment addresses (e.g., to pre-fund a contract before deployment, or to set allowances) will receive wrong addresses. Funds sent to those predicted addresses are irrecoverable.

The simulation path is reachable by any unprivileged JSON-RPC caller via `eth_simulateV1`.

---

### Likelihood Explanation

**Medium.** The trigger conditions are:

- EIP-7702 is active (Prague hardfork or equivalent).
- A simulated CALL targets an EIP-7702 delegated account whose delegated code executes at least one `CREATE` opcode.
- The simulation contains more than one call in the same block (so the stale nonce affects a subsequent call).

EIP-7702 factory patterns (delegated accounts that deploy child contracts) are a primary use-case for the feature and are already tested in the integration suite. `eth_simulateV1` is a standard JSON-RPC endpoint used by wallets, bundlers, and DeFi front-ends to preview multi-step operations.

---

### Recommendation

After `evm.Call()` returns, do **not** unconditionally overwrite the nonce. Instead, read the post-execution nonce from the stateDB (which already reflects all nested-CREATE increments) and leave it in place. The only adjustment needed is to ensure the nonce is at least `msg.Nonce+1` (to account for the call itself, which go-ethereum does not auto-increment):

```go
ret, leftoverGas, vmErr = evm.Call(msg.From, *msg.To, msg.Data, leftoverGas, value)
// Preserve any nonce increments from nested CREATEs triggered during the call.
// Only bump to msg.Nonce+1 if the call itself did not already advance the nonce.
if postNonce := sim.state.GetNonce(msg.From); postNonce < msg.Nonce+1 {
    sim.state.SetNonce(msg.From, msg.Nonce+1, tracing.NonceChangeUnspecified)
}
```

This mirrors the reconciliation logic already present in the CREATE branch of `ApplyMessageWithConfig`.

---

### Proof of Concept

**Setup:** Chain with Prague/EIP-7702 enabled. Account `A` is delegated to a factory contract `F` that executes `CREATE` in its fallback.

**Simulation request (`eth_simulateV1`):**

```json
{
  "blockStateCalls": [{
    "calls": [
      // Call 1: A calls itself (triggers F's fallback → CREATE child C1)
      { "from": "A", "to": "A", "nonce": "0x0", ... },
      // Call 2: A calls itself again (should use nonce 0x2, not 0x1)
      { "from": "A", "to": "A", "nonce": "0x2", ... }
    ]
  }]
}
```

**Trace through `applyCall` for Call 1:**

| Step | Action | Nonce |
|------|--------|-------|
| line 432 | `SetNonce(A, 0)` | 0 |
| line 449 | `evm.Call(A, A, …)` — F's CREATE increments nonce | 1 |
| line 450 | `SetNonce(A, 0+1=1)` — **overwrites** | 1 ← wrong (should be 1, but next call expects 2) |

**For Call 2** (`msg.Nonce = 2`): `sim.validate` rejects it with `ErrNonceTooHigh` (state nonce is 1, tx nonce is 2), even though on-chain execution would accept it. Alternatively, if `sim.validate = false`, the simulation proceeds with nonce 1 for Call 2, deriving a wrong child contract address for any CREATE inside Call 2.

The production execution path (`ApplyMessageWithConfig`) would leave the nonce at 2 after Call 1, making Call 2 valid and producing the correct child address. [5](#0-4) [6](#0-5) [2](#0-1)

### Citations

**File:** x/evm/keeper/simulate.go (L427-451)
```go
	// Prepare access list and transient storage
	sim.state.Prepare(rules, msg.From, evm.Context.Coinbase, msg.To, activePrecompiles, msg.AccessList)

	// Set the nonce for the sender before execution so that CREATE addresses
	// are computed correctly. evm.Create will internally bump it to nonce+1.
	sim.state.SetNonce(msg.From, msg.Nonce, tracing.NonceChangeUnspecified)

	var (
		ret   []byte
		vmErr error
	)
	if contractCreation {
		ret, _, leftoverGas, vmErr = evm.Create(msg.From, msg.Data, leftoverGas, value)
	} else {
		if msg.SetCodeAuthorizations != nil {
			for _, auth := range msg.SetCodeAuthorizations {
				if _, err := sim.keeper.applyAuthorization(&auth, sim.state); err != nil {
					sim.keeper.Logger(sim.state.Context()).Debug("simulation: failed to apply authorization",
						"error", err, "authorization", auth)
				}
			}
		}
		ret, leftoverGas, vmErr = evm.Call(msg.From, *msg.To, msg.Data, leftoverGas, value)
		sim.state.SetNonce(msg.From, msg.Nonce+1, tracing.NonceChangeUnspecified)
	}
```

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

**File:** x/evm/keeper/state_transition.go (L515-517)
```go
		// based on geth, nonce should be preincremented before evm call execution
		// which is already done on the antehandler
		ret, leftoverGas, vmErr = evm.Call(sender, *msg.To, msg.Data, leftoverGas, uint256.MustFromBig(msg.Value))
```

**File:** x/evm/keeper/set_code_authorizations.go (L48-62)
```go
func (k *Keeper) applyAuthorization(auth *types.SetCodeAuthorization, stateDB vm.StateDB) (common.Address, error) {
	authority, err := k.validateAuthorization(auth, stateDB)
	if err != nil {
		return authority, err
	}

	// If the account already exists in state, refund the new account cost
	// charged in the intrinsic calculation.
	if stateDB.Exist(authority) {
		stateDB.AddRefund(params.CallNewAccountGas - params.TxAuthTupleGas)
	}

	k.setAuthorizationDelegation(auth, authority, stateDB)
	return authority, nil
}
```

**File:** x/evm/keeper/set_code_authorizations.go (L74-77)
```go
func (k *Keeper) setAuthorizationDelegation(auth *types.SetCodeAuthorization, authority common.Address, stateDB vm.StateDB) {
	// Update nonce and account code.
	stateDB.SetNonce(authority, auth.Nonce+1, tracing.NonceChangeAuthorization)
	if auth.Address == (common.Address{}) {
```
