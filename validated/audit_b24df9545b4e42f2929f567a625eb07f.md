### Title
`eth_call` / `eth_estimateGas` Simulation Returns Success When Actual Execution Fails Due to `ErrStateConflict` — (`x/evm/keeper/state_transition.go`)

---

### Summary

`ApplyMessageWithConfig` is called with `commit=false` for simulations (`eth_call`, `eth_estimateGas`) and `commit=true` for real transaction execution. The `ErrStateConflict` check — which detects when both the outer EVM and an inner native precompile action write to the same storage slot — lives exclusively inside `stateDB.Commit()`. Because simulations never call `Commit()`, they always report success for transactions that will actually fail on-chain with a `VmError`. This is the direct Ethermint analog of the ERC4626 `previewRedeem`/`redeem` mismatch: the preview (simulation) returns an optimistic result that the actual execution cannot honor.

---

### Finding Description

In `ApplyMessageWithConfig`, the `commit` parameter controls whether `stateDB.Commit()` is invoked:

```go
// x/evm/keeper/state_transition.go
if commit {
    if err := stateDB.Commit(); err != nil {
        // Note: estimateGas and eth_call do not hit this path because commit is
        // false for simulations, so they will succeed even when a real execution
        // would produce a state conflict.
        if errors.Is(err, statedb.ErrStateConflict) {
            return &types.EVMResult{
                GasUsed:  gasUsed,
                VmError:  statedb.ErrStateConflict.Error(),
                ...
            }, nil
        }
        ...
    }
}
```

The code comment at line 609–611 explicitly acknowledges the divergence. `ErrStateConflict` is raised in `stateDB.Commit()` when both the outer EVM dirty storage and an inner native action (via `ExecuteNativeAction`) write to the same storage slot:

```go
// x/evm/statedb/statedb.go
store := s.keeper.GetState(s.ctx, obj.Address(), key)
if store != origin && store != dirty {
    return fmt.Errorf(
        "%w: address %s key %s modified by both EVM execution and native action ...",
        ErrStateConflict, ...)
}
```

The conflict check is only reachable when `commit=true`. When `commit=false` (all simulation paths), `stateDB.Commit()` is never called, so the conflict is invisible to the simulation. The simulation returns `VmError: ""` (success), while the real transaction returns `VmError: "state conflict"` (status=0, included in block but failed).

The entry path for simulations is:

- `eth_call` → `EthCall` gRPC handler → `ApplyMessageWithConfig(ctx, msg, cfg, false)`
- `eth_estimateGas` → `EstimateGas` gRPC handler → `executable(gas)` → `ApplyMessageWithConfig(ctx, msg, cfg, false)`

The entry path for real execution is:

- `MsgEthereumTx` → `ApplyTransaction` → `ApplyMessageWithConfig(tmpCtx, msg, cfg, true)`

The trigger condition — a contract that both writes an EVM storage slot and calls a native precompile (via `ExecuteNativeAction`) that writes the same slot — is reachable by any unprivileged user on a chain with stateful custom precompiles (e.g., Cronos-style bank/IBC precompiles). The `Transfer`, `AddBalance`, and `SubBalance` operations in `StateDB` already use `ExecuteNativeAction` for bank module writes; a custom precompile that additionally writes EVM storage creates the conflict.

---

### Impact Explanation

Public JSON-RPC endpoints `eth_call` and `eth_estimateGas` feed incorrect data into transaction execution decisions. Wallets, DeFi protocols, and on-chain contracts that use `eth_call` to pre-check whether a transaction will succeed will receive a false success signal. The actual submitted transaction will be included in the block with `status=0` (EVM failure), consuming all gas. For protocols that gate actions on simulation results (e.g., "simulate the swap, only proceed if it succeeds"), this mismatch can cause incorrect execution paths, failed operations, and wasted user funds (gas). This matches the allowed High impact: "Public JSON-RPC, gRPC, simulation, tracing, receipt/log, or indexer path feeds incorrect consensus-critical data into transaction execution."

---

### Likelihood Explanation

Any chain built on Ethermint that registers stateful custom precompiles using `ExecuteNativeAction` to write EVM contract storage is affected. The Cronos pattern (bank, relayer, ICA precompiles) is the documented and recommended integration path. An unprivileged user only needs to craft a transaction to a contract that both writes a storage slot in EVM bytecode and triggers a native precompile write to the same slot. No privileged access, governance, or validator compromise is required.

---

### Recommendation

Before returning from `ApplyMessageWithConfig` when `commit=false`, perform the same `ErrStateConflict` detection that `stateDB.Commit()` would perform. Specifically, add a dry-run conflict check (iterating `journal.sortedDirties()` and comparing `originStorage` vs the current store value) that runs regardless of the `commit` flag. This ensures simulation and actual execution agree on the outcome, matching the invariant that `eth_call` must return the same result as actual execution.

Alternatively, call `stateDB.Commit()` on a throwaway cache context even when `commit=false`, so the conflict detection runs but the writes are discarded.

---

### Proof of Concept

1. Deploy a contract `C` with a storage slot `S`.
2. Register a custom precompile `P` (using `ExecuteNativeAction`) that writes slot `S` of contract `C` to value `X`.
3. Contract `C` has a function `f()` that: (a) calls precompile `P` (which writes `S = X` via native action), then (b) writes `S = Y` in EVM bytecode (different value, creating a conflict).
4. Call `eth_call` targeting `C.f()`. Result: `VmError: ""`, `status: 0x1` — simulation reports success.
5. Submit the same transaction on-chain. Result: `VmError: "state conflict"`, `status: 0x0` — transaction fails, gas consumed.

The divergence is confirmed by the code comment at `x/evm/keeper/state_transition.go` lines 609–611:

> *"estimateGas and eth_call do not hit this path because commit is false for simulations, so they will succeed even when a real execution would produce a state conflict."* [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** x/evm/keeper/state_transition.go (L193-194)
```go
	// pass true to commit the StateDB
	res, err := k.ApplyMessageWithConfig(tmpCtx, msg, cfg, true)
```

**File:** x/evm/keeper/state_transition.go (L601-624)
```go
	// The dirty states in `StateDB` is either committed or discarded after return
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
	}
```

**File:** x/evm/statedb/statedb.go (L41-44)
```go
// ErrStateConflict is returned by Commit() when an EVM-dirty storage key was also
// written by a nested native action (via ExecuteNativeAction). It is treated as an
// EVM-level failure (VmError / status=0) rather than a cosmos-level rejection.
var ErrStateConflict = errors.New("state conflict")
```

**File:** x/evm/statedb/statedb.go (L766-788)
```go
	for _, addr := range s.journal.sortedDirties() {
		obj, exist := s.stateObjects[addr]
		if !exist || obj.selfDestructed {
			continue
		}
		for _, key := range obj.dirtyStorage.SortedKeys() {
			origin := obj.originStorage[key]
			dirty := obj.dirtyStorage[key]
			if dirty == origin {
				continue
			}
			// A native action wrote this slot iff the store value differs from origin.
			// If it also differs from the EVM-dirty value, the two sides disagree — conflict.
			store := s.keeper.GetState(s.ctx, obj.Address(), key)
			if store != origin && store != dirty {
				return fmt.Errorf(
					"%w: address %s key %s modified by both EVM execution and native action (origin=%s, store=%s, dirty=%s)",
					ErrStateConflict,
					obj.Address().Hex(), key.Hex(), origin.Hex(), store.Hex(), dirty.Hex(),
				)
			}
		}
	}
```

**File:** x/evm/keeper/grpc_query.go (L389-402)
```go
	executable := func(gas uint64) (vmError bool, rsp *types.EVMResult, err error) {
		// update the message with the new gas value
		msg.GasLimit = gas

		// pass false to not commit StateDB
		rsp, err = k.ApplyMessageWithConfig(ctx, msg, cfg, false)
		if err != nil {
			if errors.Is(err, core.ErrIntrinsicGas) {
				return true, nil, nil // Special case, raise gas limit
			}
			return true, nil, err // Bail out
		}
		return rsp.Failed(), rsp, nil
	}
```
