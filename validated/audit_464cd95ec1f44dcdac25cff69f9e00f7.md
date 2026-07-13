### Title
EIP-7702 `SetCodeTx` Accepted and Executed Before Prague Hardfork Is Active — Missing Transaction-Type Guard Analogous to `DynamicFeeTxType` Check - (File: `ante/interfaces/setup.go`, `x/evm/keeper/state_transition.go`)

---

### Summary

`ValidateEthBasic` rejects `DynamicFeeTxType` when London is not active (`baseFee == nil`), but contains no equivalent guard rejecting `SetCodeTxType` when Prague is not active. Simultaneously, `ApplyMessageWithConfig` processes `SetCodeAuthorizations` unconditionally — without a `rules.IsPrague` gate — meaning a pre-Prague chain will install EIP-7702 code delegations on arbitrary EOAs that have signed authorizations, mutating account code before the hardfork is supposed to permit it.

---

### Finding Description

**Root cause 1 — missing ante-handler type guard (`ante/interfaces/setup.go`)**

`ValidateEthBasic` contains a hardfork-gated type check for EIP-1559:

```go
// ante/interfaces/setup.go lines 122-124
if baseFee == nil && tx.Type() == ethtypes.DynamicFeeTxType {
    return errorsmod.Wrap(ethtypes.ErrTxTypeNotSupported, "dynamic fee tx not supported")
}
```

The analogous check for EIP-7702 is absent. The only `SetCodeTx`-specific validation that follows (lines 132–140) checks structural properties (`To == nil`, empty auth list) but never asks whether Prague rules are in effect:

```go
// ante/interfaces/setup.go lines 132-140
if tx.SetCodeAuthorizations() != nil {
    if tx.To() == nil { ... }
    if len(tx.SetCodeAuthorizations()) == 0 { ... }
}
// ← no: if !rules.IsPrague && tx.Type() == ethtypes.SetCodeTxType { reject }
``` [1](#0-0) 

**Root cause 2 — unconditional authorization processing in `ApplyMessageWithConfig` (`x/evm/keeper/state_transition.go`)**

The EVM execution path processes `SetCodeAuthorizations` with no `rules.IsPrague` guard:

```go
// x/evm/keeper/state_transition.go lines 484-513
} else {
    if msg.SetCodeAuthorizations != nil {   // ← no IsPrague check
        for _, auth := range msg.SetCodeAuthorizations {
            authority, err := k.applyAuthorization(&auth, stateDB)
            ...
        }
        if commit && cfg.DurableSetCodeAuthorizationCtx != nil && len(validAuths) > 0 {
            // writes delegation code to durable stateDB
        }
    }
    ret, leftoverGas, vmErr = evm.Call(...)
}
``` [2](#0-1) 

`applyAuthorization` → `setAuthorizationDelegation` unconditionally calls `stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), ...)`, installing delegation bytecode on the authority account: [3](#0-2) 

Neither `validateAuthorization` nor `setAuthorizationDelegation` checks `rules.IsPrague`: [4](#0-3) 

**Contrast with go-ethereum**: upstream `core/state_transition.go` wraps the entire authorization loop in `if rules.IsPrague { ... }`. Ethermint's custom `ApplyMessageWithConfig` omits this guard entirely.

---

### Impact Explanation

On a chain where `PragueTime` is set to a future timestamp (Prague not yet active), an attacker who has obtained a valid `SetCodeAuthorization` signature from any EOA (e.g., via a phishing/replay of a cross-chain authorization, or by being the authority themselves) can submit a `SetCodeTx` that:

1. Passes all ante-handler checks (no type-rejection for pre-Prague).
2. Reaches `ApplyMessageWithConfig` with `rules.IsPrague = false`.
3. Has its authorization list processed unconditionally, installing arbitrary delegation bytecode on the authority EOA.
4. Commits the delegation to the durable stateDB via `applyDurableAuthorization`.

The result is **unauthorized account code mutation** — an EOA's code is permanently set to a delegation pointer before Prague is supposed to allow this. This matches the allowed High impact: *"EIP-7702 authorization … bypass enabling … unauthorized account/code mutation."*

Additionally, the `VerifyEthAccount` EOA check is gated on `!rules.IsPrague`:

```go
// ante/eth.go lines 93-99
if !rules.IsPrague {
    if acct.IsContract() {
        return errorsmod.Wrapf(errortypes.ErrInvalidType, "the sender is not EOA: ...")
    }
}
``` [5](#0-4) 

Once a delegation is installed on an EOA via the pre-Prague exploit, that EOA becomes a "contract" account. On a subsequent non-Prague block, any transaction from that EOA would be rejected by this check, effectively bricking the account.

---

### Likelihood Explanation

- Any chain running Ethermint with a future `PragueTime` (i.e., Prague not yet activated) is vulnerable.
- The attacker only needs to craft a valid `SetCodeTx` with a signed authorization from any target EOA. The authorization signature is a standard secp256k1 ECDSA over `keccak256(MAGIC || rlp([chainID, address, nonce]))` — no privileged access required.
- The `SetCodeTx.Validate()` and `ValidateEthBasic` both pass without Prague being active.
- The `LatestSignerForChainID` signer used in Ethermint supports Prague-type signatures regardless of the current hardfork state, so signature verification does not block submission. [6](#0-5) 

---

### Recommendation

1. **In `ValidateEthBasic`** (`ante/interfaces/setup.go`), add a hardfork guard mirroring the `DynamicFeeTxType` check:

```go
if !rules.IsPrague && tx.Type() == ethtypes.SetCodeTxType {
    return errorsmod.Wrap(ethtypes.ErrTxTypeNotSupported, "EIP-7702 set code tx not supported before Prague")
}
```

2. **In `ApplyMessageWithConfig`** (`x/evm/keeper/state_transition.go`), guard the authorization processing loop with `rules.IsPrague`:

```go
if rules.IsPrague && msg.SetCodeAuthorizations != nil {
    // ... existing authorization processing
}
```

This mirrors go-ethereum's upstream behavior and closes the pre-Prague execution path.

---

### Proof of Concept

1. Deploy Ethermint with `PragueTime` set to a future block (Prague not yet active).
2. Obtain a valid `SetCodeAuthorization` signature from victim EOA `V` (e.g., `V` signed an authorization for a different chain that shares the chain ID, or `V` is the attacker's own account).
3. Construct a `SetCodeTx` with `AuthList: [{ChainID: currentChainID, Address: maliciousContract, Nonce: V.nonce, V/R/S: sig}]`.
4. Submit via `eth_sendRawTransaction`. The ante handler passes (no Prague check). `ApplyMessageWithConfig` runs with `rules.IsPrague = false` but still enters the `msg.SetCodeAuthorizations != nil` branch.
5. `applyAuthorization` validates the sig, bumps `V`'s nonce, and calls `stateDB.SetCode(V, AddressToDelegation(maliciousContract))`.
6. `applyDurableAuthorization` commits the delegation to the durable stateDB.
7. `V`'s account now has delegation bytecode installed before Prague was supposed to permit it. All subsequent calls to `V` execute `maliciousContract`'s code in `V`'s context.

### Citations

**File:** ante/interfaces/setup.go (L122-140)
```go
		if baseFee == nil && tx.Type() == ethtypes.DynamicFeeTxType {
			return errorsmod.Wrap(ethtypes.ErrTxTypeNotSupported, "dynamic fee tx not supported")
		}

		if !allowUnprotectedTxs && !tx.Protected() {
			return errorsmod.Wrapf(
				errortypes.ErrNotSupported,
				"rejected unprotected Ethereum transaction. Please EIP155 sign your transaction to protect it against replay-attacks")
		}

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

**File:** x/evm/keeper/state_transition.go (L484-513)
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
```

**File:** x/evm/keeper/set_code_authorizations.go (L14-43)
```go
// validateAuthorization validates an EIP-7702 authorization against the state.
func (k *Keeper) validateAuthorization(auth *types.SetCodeAuthorization, stateDB vm.StateDB) (authority common.Address, err error) {
	// Verify chain ID is null or equal to current chain ID.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
		return authority, core.ErrAuthorizationWrongChainID
	}
	// Limit nonce to 2^64-1 per EIP-2681.
	if auth.Nonce+1 < auth.Nonce {
		return authority, core.ErrAuthorizationNonceOverflow
	}
	// Validate signature values and recover authority.
	authority, err = auth.Authority()
	if err != nil {
		return authority, fmt.Errorf("%w: %v", core.ErrAuthorizationInvalidSignature, err)
	}
	// Check the authority account
	//  1) doesn't have code or has exisiting delegation
	//  2) matches the auth's nonce
	//
	// Note it is added to the access list even if the authorization is invalid.
	stateDB.AddAddressToAccessList(authority)
	code := stateDB.GetCode(authority)
	if _, ok := types.ParseDelegation(code); len(code) != 0 && !ok {
		return authority, core.ErrAuthorizationDestinationHasCode
	}
	if have := stateDB.GetNonce(authority); have != auth.Nonce {
		return authority, core.ErrAuthorizationNonceMismatch
	}
	return authority, nil
}
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

**File:** ante/eth.go (L93-99)
```go
		if !rules.IsPrague {
			if acct.IsContract() {
				fromAddr := common.BytesToAddress(from)
				return errorsmod.Wrapf(errortypes.ErrInvalidType,
					"the sender is not EOA: address %s, codeHash <%s>", fromAddr, acct.CodeHash)
			}
		}
```

**File:** x/evm/types/set_code_tx.go (L237-252)
```go
func (tx SetCodeTx) Validate() error {
	if len(tx.To) == 0 {
		return errorsmod.Wrap(core.ErrSetCodeTxCreate, "to address cannot be empty")
	}

	if len(tx.AuthList) == 0 {
		return errorsmod.Wrap(core.ErrEmptyAuthList, "auth list cannot be empty")
	}

	// V is the signature y-parity byte; an empty slice would panic at auth.V[0]
	// in ToEthAuthList.
	for i := range tx.AuthList {
		if len(tx.AuthList[i].V) != 1 {
			return errorsmod.Wrapf(core.ErrAuthorizationInvalidSignature, "auth %d: V must be a single byte", i)
		}
	}
```
