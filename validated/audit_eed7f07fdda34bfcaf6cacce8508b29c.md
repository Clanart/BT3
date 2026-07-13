### Title
EIP-7702 `SetCodeTxType` Missing `baseFee == nil` Rejection in `ValidateEthBasic` — (File: `ante/interfaces/setup.go`)

### Summary
`ValidateEthBasic` rejects `DynamicFeeTxType` (EIP-1559) transactions when `baseFee == nil` (fee market disabled), but applies no equivalent guard for `SetCodeTxType` (EIP-7702). Because EIP-7702 transactions share the same EIP-1559 fee mechanics (`GasFeeCap`/`GasTipCap`), the missing check allows EIP-7702 transactions — including their authorization-list processing that mutates account code — to commit to state in a context where all EIP-1559-style transactions are supposed to be rejected.

### Finding Description

In `ante/interfaces/setup.go`, `ValidateEthBasic` contains the following guard:

```go
if baseFee == nil && tx.Type() == ethtypes.DynamicFeeTxType {
    return errorsmod.Wrap(ethtypes.ErrTxTypeNotSupported, "dynamic fee tx not supported")
}
``` [1](#0-0) 

`SetCodeTxType` (EIP-7702) is structurally identical to `DynamicFeeTxType` from a fee-mechanics perspective: both carry `GasFeeCap` and `GasTipCap` and require a live base fee to compute the effective gas price. No analogous check exists for `SetCodeTxType`.

When `baseFee == nil` (e.g., `NoBaseFee = true` in fee market params), the ante handler correctly blocks `DynamicFeeTxType` but silently passes `SetCodeTxType` through. The transaction then reaches `ApplyMessageWithConfig`, where Ethermint's own authorization-processing loop runs **before** `evm.Call`:

```go
if msg.SetCodeAuthorizations != nil {
    for _, auth := range msg.SetCodeAuthorizations {
        authority, err := k.applyAuthorization(&auth, stateDB)
        ...
    }
}
ret, leftoverGas, vmErr = evm.Call(...)
``` [2](#0-1) 

`applyAuthorization` calls `setAuthorizationDelegation`, which unconditionally writes `SetNonce` and `SetCode` to the stateDB:

```go
stateDB.SetNonce(authority, auth.Nonce+1, tracing.NonceChangeAuthorization)
stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
``` [3](#0-2) 

These writes are committed to the Cosmos KV store via `stateDB.Commit()` at the end of `ApplyMessageWithConfig`. When hooks are active, the durable authorization context ensures the delegation survives even a post-hook failure:

```go
if len(msg.SetCodeAuthorizations) > 0 {
    var durableAuthorizationCtx sdk.Context
    durableAuthorizationCtx, commitDurableAuthorization = ctx.CacheContext()
    cfg.DurableSetCodeAuthorizationCtx = &durableAuthorizationCtx
}
``` [4](#0-3) 

The ante handler check for EIP-7702 in `ValidateEthBasic` only validates structural properties (non-nil `To`, non-empty auth list) and does not include the `baseFee == nil` guard:

```go
if tx.SetCodeAuthorizations() != nil {
    if tx.To() == nil { ... }
    if len(tx.SetCodeAuthorizations()) == 0 { ... }
}
``` [5](#0-4) 

### Impact Explanation

When `NoBaseFee = true` (fee market disabled, `baseFee == nil`):

- `DynamicFeeTxType` is rejected at the ante handler — no state changes occur.
- `SetCodeTxType` passes the ante handler, reaches `ApplyMessageWithConfig`, and the authorization loop executes. Valid EIP-7702 authorizations cause `SetNonce` + `SetCode` writes that are committed to the Cosmos store, permanently installing delegation bytecode (`0xef0100<addr>`) on the authority account.

This is an ante handler bug that permits an invalid transaction type (EIP-1559-style when fee market is disabled) to commit state, specifically mutating account code via EIP-7702 delegation. It matches the allowed High impact: *"ante handler… bug that permits invalid transactions to commit."*

### Likelihood Explanation

- Requires `NoBaseFee = true` in fee market params, a documented and supported configuration for chains that want a static or zero base fee.
- Any unprivileged user who controls an EOA can craft and submit a valid EIP-7702 transaction. No special privileges are needed beyond a funded account and a valid authorization signature.
- The attacker-controlled entry path is a standard JSON-RPC `eth_sendRawTransaction` call with a type-4 transaction.

### Recommendation

Extend the `baseFee == nil` guard in `ValidateEthBasic` to cover `SetCodeTxType`:

```go
if baseFee == nil && (tx.Type() == ethtypes.DynamicFeeTxType ||
    tx.Type() == ethtypes.SetCodeTxType) {
    return errorsmod.Wrap(ethtypes.ErrTxTypeNotSupported,
        "EIP-1559/EIP-7702 tx not supported: base fee is nil")
}
``` [1](#0-0) 

### Proof of Concept

1. Deploy an Ethermint chain with `NoBaseFee = true` in fee market params (base fee keeper returns `nil`).
2. Fund EOA `authority` and EOA `sender`.
3. Sign an EIP-7702 authorization: `auth = SignSetCode(authorityKey, {ChainID: chainID, Address: delegateAddr, Nonce: 0})`.
4. Construct a type-4 (`SetCodeTxType`) transaction from `sender` to `authority` carrying `auth`.
5. Submit via `eth_sendRawTransaction`.
6. **Observed**: Transaction passes `ValidateEthBasic` (no `baseFee == nil` rejection for `SetCodeTxType`), `applyAuthorization` runs, `authority`'s code is set to `0xef0100<delegateAddr>`, and the nonce is bumped to 1 — all committed to state.
7. **Expected**: Transaction should be rejected at the ante handler with `ErrTxTypeNotSupported`, identical to what happens for a `DynamicFeeTxType` transaction under the same `NoBaseFee = true` configuration.

### Citations

**File:** ante/interfaces/setup.go (L122-124)
```go
		if baseFee == nil && tx.Type() == ethtypes.DynamicFeeTxType {
			return errorsmod.Wrap(ethtypes.ErrTxTypeNotSupported, "dynamic fee tx not supported")
		}
```

**File:** ante/interfaces/setup.go (L132-140)
```go
		// Check that EIP-7702 authorization list signatures are well formed.
		if tx.SetCodeAuthorizations() != nil {
			if tx.To() == nil {
				return errorsmod.Wrapf(errortypes.ErrInvalidRequest, "EIP-7702 set code transaction cannot be contract creation (sender %v)", msgEthTx.From)
			}
			if len(tx.SetCodeAuthorizations()) == 0 {
				return errorsmod.Wrapf(errortypes.ErrInvalidRequest, "EIP-7702 authorization list cannot be empty (sender %v)", msgEthTx.From)
			}
		}
```

**File:** x/evm/keeper/state_transition.go (L186-190)
```go
		if len(msg.SetCodeAuthorizations) > 0 {
			var durableAuthorizationCtx sdk.Context
			durableAuthorizationCtx, commitDurableAuthorization = ctx.CacheContext()
			cfg.DurableSetCodeAuthorizationCtx = &durableAuthorizationCtx
		}
```

**File:** x/evm/keeper/state_transition.go (L484-517)
```go
		if msg.SetCodeAuthorizations != nil {
			// Track validated authorizations together with the authority recovered
			// during validation, so the durable replay below can reuse it.
			type validAuth struct {
				auth      ethtypes.SetCodeAuthorization
				authority common.Address
			}
			var validAuths []validAuth
			for _, auth := range msg.SetCodeAuthorizations {
				// Note errors are ignored, we simply skip invalid authorizations here.
				authority, err := k.applyAuthorization(&auth, stateDB)
				if err != nil {
					k.Logger(ctx).Debug("failed to apply authorization", "error", err, "authorization", auth)
					continue
				}
				validAuths = append(validAuths, validAuth{auth: auth, authority: authority})
			}

			if commit && cfg.DurableSetCodeAuthorizationCtx != nil && len(validAuths) > 0 {
				durableStateDB := statedb.NewWithParams(*cfg.DurableSetCodeAuthorizationCtx, k, cfg.TxConfig, cfg.Params.EvmDenom)
				for _, va := range validAuths {
					// Replay the already-validated effects; this cannot fail, so it
					// mirrors the main loop's skip-on-invalid behavior without ever
					// turning an EVM-level outcome into a cosmos-level tx error.
					k.applyDurableAuthorization(&va.auth, va.authority, durableStateDB)
				}
				if err := durableStateDB.Commit(); err != nil {
					return nil, errorsmod.Wrap(err, "failed to commit durable EIP-7702 authorization stateDB")
				}
			}
		}
		// based on geth, nonce should be preincremented before evm call execution
		// which is already done on the antehandler
		ret, leftoverGas, vmErr = evm.Call(sender, *msg.To, msg.Data, leftoverGas, uint256.MustFromBig(msg.Value))
```

**File:** x/evm/keeper/set_code_authorizations.go (L74-85)
```go
func (k *Keeper) setAuthorizationDelegation(auth *types.SetCodeAuthorization, authority common.Address, stateDB vm.StateDB) {
	// Update nonce and account code.
	stateDB.SetNonce(authority, auth.Nonce+1, tracing.NonceChangeAuthorization)
	if auth.Address == (common.Address{}) {
		// Delegation to zero address means clear.
		stateDB.SetCode(authority, nil, tracing.CodeChangeAuthorizationClear)
		return
	}

	// Otherwise install delegation to auth.Address.
	stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
}
```
