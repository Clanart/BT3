### Title
uint64 Underflow in `nestedCreates` Nonce Reconciliation Corrupts Sender Nonce on Failed Batched CREATE - (File: `x/evm/keeper/state_transition.go`)

---

### Summary

`ApplyMessageWithConfig` resets the sender's nonce to `msg.Nonce` before calling `evm.Create()`, then reconciles it afterward using an unchecked uint64 subtraction. When `evm.Create()` returns without incrementing the nonce (because the EVM-level balance check fails mid-batch), the subtraction underflows to `2^64 - 1`, and the subsequent addition overflows, writing `oldNonce - 1` back to the stateDB. This permanently corrupts the sender's on-chain nonce, enabling nonce reuse.

---

### Finding Description

The nonce reconciliation block in `ApplyMessageWithConfig` is:

```go
// x/evm/keeper/state_transition.go lines 468-482
if contractCreation {
    oldNonce := stateDB.GetNonce(sender)
    stateDB.SetNonce(sender, msg.Nonce, tracing.NonceChangeUnspecified)
    ret, _, leftoverGas, vmErr = evm.Create(sender, msg.Data, leftoverGas, uint256.MustFromBig(msg.Value))
    afterCreateNonce := stateDB.GetNonce(sender)
    nestedCreates := afterCreateNonce - msg.Nonce - 1   // ← uint64, no underflow guard
    stateDB.SetNonce(sender, oldNonce+nestedCreates, tracing.NonceChangeUnspecified)
}
``` [1](#0-0) 

The comment on line 476 assumes `evm.Create()` always increments the nonce, so `afterCreateNonce >= msg.Nonce + 1`. This assumption is violated.

In go-ethereum's `evm.create()`, the nonce is incremented **after** the depth and balance checks:

```
if depth > CallCreateDepth  → return ErrDepth          (nonce NOT incremented)
if !CanTransfer(value)      → return ErrInsufficientBalance (nonce NOT incremented)
StateDB.SetNonce(nonce + 1) ← only reached if both checks pass
```

For a Cosmos batch of two CREATE messages from the same sender (nonces 0 and 1, each with value `V`), the ante handler's `CheckEthCanTransfer` checks each message's value against the **same pre-execution balance** (it does not simulate value consumption between messages):

```go
// ante/eth.go lines 242-249
if value.Sign() > 0 && !canTransfer(ctx, evmKeeper, evmParams.EvmDenom, from, value) {
    return errorsmod.Wrapf(errortypes.ErrInsufficientFunds, ...)
}
``` [2](#0-1) 

If the sender's post-fee balance `B` satisfies `V ≤ B < 2V`, both ante-handler checks pass. During execution, message 0 succeeds and transfers `V`, leaving balance `B - V < V`. When message 1 executes, `evm.Create()` calls `CanTransfer` against the now-reduced stateDB balance, which fails, and returns `ErrInsufficientBalance` **without incrementing the nonce**.

At that point:
- `afterCreateNonce = msg.Nonce` (= 1, unchanged)
- `nestedCreates = 1 - 1 - 1 = 2^64 - 1` (uint64 underflow)
- `oldNonce = 2` (ante handler incremented both messages)
- `stateDB.SetNonce(sender, 2 + (2^64 - 1)) = stateDB.SetNonce(sender, 1)` (uint64 overflow)

The stateDB is then committed unconditionally (the `commit` path does not gate on `vmErr`):

```go
// x/evm/keeper/state_transition.go lines 602-623
if commit {
    if err := stateDB.Commit(); err != nil { ... }
}
``` [3](#0-2) 

After commit, the Cosmos account sequence is 1 instead of 2. The nonce-1 slot is now reusable.

---

### Impact Explanation

The sender's committed on-chain nonce is set to `oldNonce - 1` instead of `oldNonce`. This means:

1. **Nonce reuse**: The sender can submit a new transaction with the recycled nonce. Any previously signed-but-not-broadcast transaction carrying that nonce (e.g., a token transfer, a different contract deployment) can now be replayed or a fresh transaction can be injected at that nonce slot.
2. **Nonce gap / account freeze**: If the sender does not exploit the recycled nonce, their account sequence is one behind the expected value, causing all subsequent transactions to be rejected as "nonce too high" until the gap is filled.
3. **State corruption**: The Cosmos account sequence diverges from the EVM-visible nonce, breaking invariants relied upon by the ante handler's `CheckAndSetEthSenderNonce`.

This matches the allowed High impact: *"EVM state transition … ante handler … bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

The trigger requires:
- A Cosmos batch transaction (two or more `MsgEthereumTx` messages in one Cosmos tx) — a supported and documented feature.
- Both messages are contract creations (`To == nil`) with non-zero `value`.
- The sender's balance satisfies `V ≤ B - fees < 2V`.

An attacker controlling their own account can craft this deliberately. The condition is easy to satisfy: fund the account with exactly `1.5V`, set each CREATE's value to `V`. No privileged access, no validator collusion, and no external dependency is required. The batch transaction path is reachable via the standard Cosmos SDK broadcast endpoint.

---

### Recommendation

Guard the subtraction against the case where `evm.Create()` did not increment the nonce:

```go
afterCreateNonce := stateDB.GetNonce(sender)
var nestedCreates uint64
if afterCreateNonce > msg.Nonce {
    nestedCreates = afterCreateNonce - msg.Nonce - 1
}
// If afterCreateNonce <= msg.Nonce, evm.Create failed before incrementing;
// nestedCreates stays 0 and oldNonce is restored unchanged.
stateDB.SetNonce(sender, oldNonce+nestedCreates, tracing.NonceChangeUnspecified)
```

Additionally, `CheckEthCanTransfer` should accumulate consumed value across messages in a batch before checking each message, mirroring the actual execution order.

---

### Proof of Concept

1. Fund account `A` with balance `B = 1.5 * 10^18 wei` (1.5 ETH). Fees are negligible.
2. Construct a Cosmos batch transaction with two `MsgEthereumTx` messages:
   - `msg0`: `To=nil`, `Nonce=0`, `Value=1e18` (1 ETH), any valid init code.
   - `msg1`: `To=nil`, `Nonce=1`, `Value=1e18` (1 ETH), any valid init code.
3. Broadcast via `cosmos broadcast`.
4. Ante handler passes: `CheckEthCanTransfer` checks `1.5e18 >= 1e18` for both messages independently.
5. `msg0` executes: `evm.Create()` succeeds, balance becomes `0.5e18`, nonce set to 2.
6. `msg1` executes:
   - `oldNonce = 2`
   - `stateDB.SetNonce(A, 1)` (reset to msg.Nonce)
   - `evm.Create()` → `CanTransfer(0.5e18, 1e18)` fails → returns `ErrInsufficientBalance`, nonce stays at 1
   - `afterCreateNonce = 1`, `nestedCreates = 1 - 1 - 1 = 2^64 - 1`
   - `stateDB.SetNonce(A, 2 + 2^64 - 1) = stateDB.SetNonce(A, 1)`
7. stateDB committed. Account `A`'s sequence is now **1**.
8. Attacker broadcasts a new transaction with `Nonce=1` — it is accepted, reusing the nonce slot.

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

**File:** x/evm/keeper/state_transition.go (L602-623)
```go
	if commit {
		if err := stateDB.Commit(); err != nil {
			// A state conflict between the outer EVM and a nested native action is an
			// EVM-level failure: surface it as a VmError so the transaction is included
			// in the block with status=0 rather than rejected at the cosmos message level.
			// All other commit errors (infrastructure failures) remain cosmos-level errors.
			//
			// Note: estimateGas and eth_call do not hit this path because commit is
			// false for simulations, so they will succeed even when a real execution
			// would produce a state conflict.
			if errors.Is(err, statedb.ErrStateConflict) {
				return &types.EVMResult{
					GasUsed:          gasUsed,
					VmError:          statedb.ErrStateConflict.Error(),
					Hash:             cfg.TxConfig.TxHash.Hex(),
					BlockHash:        ctx.HeaderHash(),
					ExecutionGasUsed: temporaryGasUsed,
				}, nil
			}

			return nil, errorsmod.Wrap(err, "failed to commit stateDB")
		}
```

**File:** ante/eth.go (L242-249)
```go
		if value.Sign() > 0 && !canTransfer(ctx, evmKeeper, evmParams.EvmDenom, from, value) {
			return errorsmod.Wrapf(
				errortypes.ErrInsufficientFunds,
				"failed to transfer %s from address %s using the EVM block context transfer function",
				value,
				from,
			)
		}
```
