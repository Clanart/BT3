Based on my investigation, I found a concrete analog to the "stale flag not cleared" bug pattern in `x/erc20/keeper/token_pairs.go`.

### Title
`DeleteTokenPair` fails to clear dynamic/native precompile registration flags, leaving a stale precompile marker reusable by attacker-controlled state - (File: `x/erc20/keeper/token_pairs.go`)

### Summary
The Lend contract bug is a class of "incomplete state clearing": one code path (repay) fully resets a state flag while another code path (liquidate) leaves the flag set, so later reuse of the same key encounters half-initialized state. `DeleteTokenPair` in the ERC20 keeper exhibits the same pattern: it clears the token pair record, the ERC20→ID map, the denom→ID map, and allowances, but does **not** call `DeleteDynamicPrecompile`/`DeleteNativePrecompile` to clear the `KeyPrefixDynamicPrecompiles`/`KeyPrefixNativePrecompiles` flag for that address. [1](#0-0) 

### Finding Description
`DeleteTokenPair` is invoked whenever a native-ERC20-backed token pair's underlying contract is found to be self-destructed, both from `ConvertERC20` and `ConvertCoin`: [2](#0-1) [3](#0-2) 

This deletion path removes the token-pair record and its lookup maps, but the dynamic-precompile "enabled" flag set via `EnableDynamicPrecompile`/`SetDynamicPrecompile` (stored under `KeyPrefixDynamicPrecompiles`) is never removed: [4](#0-3) [5](#0-4) 

Once an address is left in the dynamic-precompile set with no backing token pair, that address continues to be treated as an active EVM precompile (a privileged execution path with special gas/dispatch semantics) even though `GetTokenPair`/`GetERC20Map` for that address now returns "not found". Any contract creation, `SELFDESTRUCT` recreation (pre-Cancun) or address-reuse mechanic that lands code at that exact address would then execute under precompile dispatch rather than as ordinary EVM bytecode, or — depending on how the precompile registry is consulted during EVM call dispatch — could shadow/hijack calls intended for a legitimately redeployed contract at that same address. I was unable to fully trace the EVM keeper's precompile dispatch table (`x/vm/keeper/static_precompiles.go` and the corresponding dynamic-precompile lookup used during `EVM.Call`) within available context to confirm whether stale entries are filtered against `IsTokenPairRegistered` at call time, so I cannot conclusively confirm a Critical fund-theft/duplication path is reachable versus merely a leftover no-op flag.

### Impact Explanation
If the stale dynamic-precompile flag is consulted by the EVM call path without re-validating that a live token pair backs it, this could allow an unprivileged user to have arbitrary bytecode dispatched through the ERC20-precompile handler for an address with no real TokenPair, or could block/shadow normal contract deployment at that specific address — either of which would corrupt balance/accounting invariants for the address in question. Given the significant uncertainty about how the EVM keeper consults `IsDynamicPrecompileAvailable` during call dispatch (not confirmed in this pass), the severity here is not confirmed as Critical.

### Likelihood Explanation
Reaching a self-destructed native-ERC20 token pair is achievable by a normal user (deploy a mintable/burnable/native ERC20, register a token pair, self-destruct the contract, then trigger `ConvertERC20`/`ConvertCoin` to invoke `DeleteTokenPair`), so the precondition (stale flag left behind) is trivially reachable without privilege. However, the follow-on step — getting new code deployed at that exact address and having the EVM actually honor the stale precompile flag over the freshly deployed bytecode — requires confirmation of the dispatch-order logic I could not fully verify.

### Recommendation
In `DeleteTokenPair` (`x/erc20/keeper/token_pairs.go`), also call `k.DeleteDynamicPrecompile(ctx, tokenPair.GetERC20Contract())` and, where applicable, `k.DeleteNativePrecompile(ctx, tokenPair.GetERC20Contract())`, mirroring the fix pattern from the reference report (clear the "enabled/deployed" flag in the same clean-up transaction that clears the underlying record). Additionally, the EVM's precompile dispatch logic should defensively re-validate `IsTokenPairRegistered`/`GetTokenPair` existence at call time rather than trusting the standalone enabled-flag store.

### Proof of Concept
1. Deploy a native ERC20 contract and register it as a token pair (`MsgRegisterERC20` or an owner-registered flow), which calls `EnableDynamicPrecompile` and sets the `KeyPrefixDynamicPrecompiles` flag for the contract address.
2. Self-destruct the ERC20 contract (e.g., via a `selfdestruct` call in the contract), removing its code/account.
3. Call `MsgConvertERC20` or `MsgConvertCoin` referencing that pair; since `acc.HasCodeHash()` is false, `DeleteTokenPair` runs, removing the pair/denom/ERC20 maps and allowances — but leaving the `KeyPrefixDynamicPrecompiles` entry for that address untouched (verifiable via `GetErc20Keeper().IsDynamicPrecompileAvailable(ctx, addr)` returning `true` post-deletion, while `IsTokenPairRegistered`/`IsERC20Registered` return `false`).
4. This confirms the incomplete-clearing defect; further work is required (not completed in this pass) to confirm whether the EVM call-dispatch path treats this stale flag as authoritative in a way that produces a Critical accounting or execution-hijack impact, or whether it is a harmless dangling marker.

### Citations

**File:** x/erc20/keeper/token_pairs.go (L110-117)
```go
// DeleteTokenPair removes a token pair.
func (k Keeper) DeleteTokenPair(ctx sdk.Context, tokenPair types.TokenPair) {
	id := tokenPair.GetID()
	k.deleteTokenPair(ctx, id)
	k.deleteERC20Map(ctx, tokenPair.GetERC20Contract())
	k.deleteDenomMap(ctx, tokenPair.Denom)
	k.deleteAllowances(ctx, tokenPair.GetERC20Contract())
}
```

**File:** x/erc20/keeper/msg_server.go (L42-53)
```go
	if pair.IsNativeERC20() {
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```

**File:** x/erc20/keeper/msg_server.go (L209-220)
```go
	case pair.IsNativeERC20():
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```

**File:** x/erc20/keeper/precompiles.go (L132-140)
```go
// EnableDynamicPrecompile adds the address of the given precompile to the prefix store
func (k Keeper) EnableDynamicPrecompile(ctx sdk.Context, address common.Address) error {
	k.Logger(ctx).Info("Added new precompiles", "addresses", address)
	if err := k.RegisterCodeHash(ctx, address, PrecompileTypeDynamic); err != nil {
		return err
	}
	k.SetDynamicPrecompile(ctx, address)
	return nil
}
```

**File:** x/erc20/keeper/precompiles.go (L157-170)
```go
func (k Keeper) IsDynamicPrecompileAvailable(ctx sdk.Context, precompile common.Address) bool {
	store := prefix.NewStore(ctx.KVStore(k.storeKey), types.KeyPrefixDynamicPrecompiles)
	return store.Has([]byte(precompile.Hex()))
}

func (k Keeper) SetDynamicPrecompile(ctx sdk.Context, precompile common.Address) {
	store := prefix.NewStore(ctx.KVStore(k.storeKey), types.KeyPrefixDynamicPrecompiles)
	store.Set([]byte(precompile.Hex()), isTrue)
}

func (k Keeper) DeleteDynamicPrecompile(ctx sdk.Context, precompile common.Address) {
	store := prefix.NewStore(ctx.KVStore(k.storeKey), types.KeyPrefixDynamicPrecompiles)
	store.Delete([]byte(precompile.Hex()))
}
```
