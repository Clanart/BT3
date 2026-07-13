### Title
Missing `SetCodeTxType` in `baseFee == nil` Guard Allows EIP-7702 Authorizations to Execute When Prague Is Not Active - (File: `ante/interfaces/setup.go`)

### Summary

`ValidateEthBasic` guards against dynamic-fee transaction types when the fee market is disabled (`baseFee == nil`), but the guard only names `DynamicFeeTxType` (type 2). The newly added `SetCodeTxType` (type 4, EIP-7702) is also a dynamic-fee type and is not included in the filter. A crafted EIP-7702 transaction therefore passes the ante handler on a chain where London/Prague is not active, and Ethermint's custom authorization-processing code in `ApplyMessageWithConfig` applies the code delegations unconditionally — without any `rules.IsPrague` guard — mutating EOA code storage in a state that was never supposed to support EIP-7702.

### Finding Description

**Root cause — incomplete type filter in `ValidateEthBasic`** [1](#0-0) 

```go
if baseFee == nil && tx.Type() == ethtypes.DynamicFeeTxType {
    return errorsmod.Wrap(ethtypes.ErrTxTypeNotSupported, "dynamic fee tx not supported")
}
```

`ethtypes.DynamicFeeTxType` is the integer constant `2`. `ethtypes.SetCodeTxType` is `4`. The condition is never true for a type-4 transaction, so a `SetCodeTx` submitted when `baseFee == nil` (London hardfork disabled, or `NoBaseFee = true` in fee-market params) is silently admitted past this guard.

**Unconditional authorization processing in `ApplyMessageWithConfig`** [2](#0-1) 

```go
if msg.SetCodeAuthorizations != nil {
    ...
    for _, auth := range msg.SetCodeAuthorizations {
        authority, err := k.applyAuthorization(&auth, stateDB)
        ...
    }
    if commit && cfg.DurableSetCodeAuthorizationCtx != nil && len(validAuths) > 0 {
        ...
        k.applyDurableAuthorization(&va.auth, va.authority, durableStateDB)
        ...
        durableStateDB.Commit()
    }
}
```

There is no `rules.IsPrague` check before this block. Whenever `msg.SetCodeAuthorizations != nil`, the keeper calls `applyAuthorization` and then commits the delegation code to the durable state DB, regardless of whether the Prague hardfork is active.

**`applyAuthorization` / `setAuthorizationDelegation` write code to EOA accounts** [3](#0-2) 

```go
func (k *Keeper) setAuthorizationDelegation(auth *types.SetCodeAuthorization, authority common.Address, stateDB vm.StateDB) {
    stateDB.SetNonce(authority, auth.Nonce+1, tracing.NonceChangeAuthorization)
    if auth.Address == (common.Address{}) {
        stateDB.SetCode(authority, nil, tracing.CodeChangeAuthorizationClear)
        return
    }
    stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
}
```

This permanently writes delegation bytecode (`0xef0100 ++ address`) into the code slot of the authority account and bumps its nonce, both committed to the durable KV store.

**The other tx-type checks do not compensate**

The `allowUnprotectedTxs` guard is irrelevant because `SetCodeTx` is always EIP-155 protected. The `enableCall` guard fires only when `tx.To() == nil`, but `SetCodeTx` always has a non-nil `To` (enforced by the EIP-7702 check at line 134). No other ante decorator rejects a type-4 tx when Prague is inactive. [4](#0-3) 

### Impact Explanation

An unprivileged attacker who submits a well-formed EIP-7702 transaction to a chain where `baseFee == nil` (fee market disabled via `NoBaseFee = true`, or London hardfork not yet activated) can:

1. Cause `SetCode` delegation bytecode to be written into any EOA whose private key the attacker controls (the authority signer in the authorization list).
2. Bump the nonce of that EOA as a side-effect of `setAuthorizationDelegation`.
3. Commit these mutations to the durable state DB even though the chain's consensus rules do not permit EIP-7702 at that block height.

The result is unauthorized account/code mutation: EOA accounts acquire contract-like delegation code in a protocol state that was never supposed to support it, which can break downstream invariants (e.g., the `IsContract()` check in `VerifyEthAccount` that guards against contract senders when `!rules.IsPrague`). [5](#0-4) 

This matches the allowed High impact: *"EIP-7702 authorization … bypass enabling … unauthorized account/code mutation."*

### Likelihood Explanation

- `NoBaseFee = true` is a documented, supported configuration for Ethermint chains that do not want EIP-1559 fee market behavior. Any such chain that also activates Prague (or that an operator intends to upgrade to Prague) is directly exposed.
- The attack requires only the ability to submit a raw Ethereum transaction via `eth_sendRawTransaction` — fully unprivileged.
- The attacker needs to control the private key of the authority account they wish to mutate, so self-targeted code injection is the primary vector; however, the nonce side-effect and the state inconsistency affect the whole chain.

### Recommendation

Add `ethtypes.SetCodeTxType` to the guard in `ValidateEthBasic`:

```go
if baseFee == nil && (tx.Type() == ethtypes.DynamicFeeTxType || tx.Type() == ethtypes.SetCodeTxType) {
    return errorsmod.Wrap(ethtypes.ErrTxTypeNotSupported, "dynamic fee tx not supported")
}
```

Additionally, add a `rules.IsPrague` guard in `ApplyMessageWithConfig` before processing `SetCodeAuthorizations`:

```go
if msg.SetCodeAuthorizations != nil && rules.IsPrague {
    // ... existing authorization processing
}
```

### Proof of Concept

1. Deploy an Ethermint chain with `NoBaseFee = true` (fee market disabled) and Prague hardfork activated in the chain config.
2. Generate a key pair `(privKey, authority)`.
3. Craft an EIP-7702 transaction:
   - `type = 4` (`SetCodeTxType`)
   - `to = <any address>`
   - `authorizationList = [SignSetCode(privKey, {chainID, delegateAddr, nonce})]`
   - `gasFeeCap = 0`, `gasTipCap = 0` (passes fee checks because `baseFee == nil`)
4. Submit via `eth_sendRawTransaction`.
5. The ante handler passes: the `baseFee == nil && tx.Type() == DynamicFeeTxType` guard is false (type is 4, not 2); all other guards pass.
6. `ApplyMessageWithConfig` processes the authorization list without a `rules.IsPrague` check, calling `setAuthorizationDelegation` which writes `0xef0100 ++ delegateAddr` into `authority`'s code slot and commits it to the durable state DB.
7. Observe that `eth_getCode(authority)` now returns delegation bytecode on a chain that was supposed to have no EIP-7702 support. [1](#0-0) [6](#0-5) [3](#0-2)

### Citations

**File:** ante/interfaces/setup.go (L122-124)
```go
		if baseFee == nil && tx.Type() == ethtypes.DynamicFeeTxType {
			return errorsmod.Wrap(ethtypes.ErrTxTypeNotSupported, "dynamic fee tx not supported")
		}
```

**File:** ante/interfaces/setup.go (L126-140)
```go
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

**File:** x/evm/keeper/state_transition.go (L484-514)
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
