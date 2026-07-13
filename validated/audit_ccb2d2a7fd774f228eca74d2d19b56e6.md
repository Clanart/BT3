### Title
`EnableCreate` Governance Restriction Bypassed via EIP-7702 Authorization Code Installation — (`x/evm/keeper/state_transition.go`)

### Summary

When the governance parameter `EnableCreate` is set to `false` to block new contract deployments, an unprivileged user can still install code on an EOA by submitting a Type 4 (EIP-7702) transaction. The `EnableCreate` guard only checks `msg.To == nil` (the contract-creation path), while EIP-7702 authorization processing runs unconditionally on the call path (`msg.To != nil`), writing delegation bytecode to arbitrary EOAs via `stateDB.SetCode`.

### Finding Description

`ApplyMessageWithConfig` enforces the governance restriction at the top of the function:

```go
// x/evm/keeper/state_transition.go L352-356
if !cfg.Params.EnableCreate && msg.To == nil {
    return nil, errorsmod.Wrap(types.ErrCreateDisabled, "failed to create new contract")
} else if !cfg.Params.EnableCall && msg.To != nil {
    return nil, errorsmod.Wrap(types.ErrCallDisabled, "failed to call contract")
}
```

A Type 4 (EIP-7702) transaction always has `msg.To != nil` (it calls a target contract). Therefore, when `EnableCreate = false` but `EnableCall = true`, the `EnableCreate` branch is never entered and execution continues.

Immediately after, in the `else` branch (call path), EIP-7702 authorizations are processed:

```go
// x/evm/keeper/state_transition.go L484-513
if msg.SetCodeAuthorizations != nil {
    for _, auth := range msg.SetCodeAuthorizations {
        authority, err := k.applyAuthorization(&auth, stateDB)
        ...
    }
    ...
}
```

`applyAuthorization` calls `setAuthorizationDelegation`, which unconditionally writes delegation bytecode to the authority EOA:

```go
// x/evm/keeper/set_code_authorizations.go L84
stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
```

This `SetCode` call installs code on the authority account and is committed to state via `stateDB.Commit()`, regardless of the `EnableCreate` governance flag.

The analog to the external report is exact:
- **Restricted path**: `msg.To == nil` (contract creation) — blocked by `EnableCreate = false`.
- **Bypass path**: `msg.To != nil` with `SetCodeAuthorizations` (EIP-7702 call) — not blocked, installs code on EOAs.

### Impact Explanation

When a chain operator disables `EnableCreate` (e.g., as an emergency measure to halt new code deployments), any unprivileged user can still install delegation code on their own EOA by submitting a Type 4 transaction. The EOA then behaves as a smart contract (executing the delegated code on every call), creating a new code-execution path that the governance restriction was intended to prevent. This constitutes **unauthorized account/code mutation** — a High impact per the allowed scope.

### Likelihood Explanation

- Requires only a standard Type 4 Ethereum transaction, which any EOA holder can submit.
- No privileged role, leaked key, or validator collusion needed.
- The attacker only needs to know that `EnableCreate` is disabled (observable on-chain) and craft a valid EIP-7702 authorization signed by their own key.
- Likelihood is **High** given the trivial entry path.

### Recommendation

Extend the governance guard to also reject EIP-7702 authorization processing when `EnableCreate` is disabled. Before iterating over `msg.SetCodeAuthorizations`, add:

```go
if !cfg.Params.EnableCreate && len(msg.SetCodeAuthorizations) > 0 {
    return nil, errorsmod.Wrap(types.ErrCreateDisabled,
        "EIP-7702 code delegation disabled: contract creation is disabled")
}
```

This mirrors the existing pattern and closes the bypass path without affecting normal call transactions.

### Proof of Concept

1. Governance submits a parameter-change proposal setting `EnableCreate = false`; it passes and takes effect.
2. Attacker holds EOA `A` with nonce `N`. They sign an EIP-7702 authorization: `{ChainID: chainID, Address: existingContractX, Nonce: N}`.
3. Attacker broadcasts a Type 4 transaction: `{To: someAddress, SetCodeAuthorizations: [signedAuth]}`.
4. In `ApplyMessageWithConfig`:
   - `msg.To != nil` → `EnableCreate` check is skipped entirely.
   - `EnableCall` is `true` → `EnableCall` check also passes.
   - `applyAuthorization` validates the signature, recovers authority `A`, and calls `stateDB.SetCode(A, delegation(existingContractX), ...)`.
5. `stateDB.Commit()` persists the delegation code to `A`'s account in the Cosmos KV store.
6. EOA `A` now has code installed and behaves as a smart contract delegating to `existingContractX`, despite `EnableCreate = false`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** x/evm/keeper/state_transition.go (L351-356)
```go
	// return error if contract creation or call are disabled through governance
	if !cfg.Params.EnableCreate && msg.To == nil {
		return nil, errorsmod.Wrap(types.ErrCreateDisabled, "failed to create new contract")
	} else if !cfg.Params.EnableCall && msg.To != nil {
		return nil, errorsmod.Wrap(types.ErrCallDisabled, "failed to call contract")
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

**File:** x/evm/keeper/set_code_authorizations.go (L74-84)
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
```
