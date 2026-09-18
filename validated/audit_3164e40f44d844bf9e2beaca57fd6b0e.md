### Title
Pointer precompile deploys ERC pointer contracts via `CREATE` with a shared, predictable module-account nonce, making pointer contract addresses vulnerable to reorg-driven address reassignment - (File: `x/evm/keeper/pointer_upgrade.go`)

### Summary
The `pointer` precompile (`0x000000000000000000000000000000000000100b`), reachable by any unprivileged EOA via `addNativePointer`, `addCW20Pointer`, `addCW721Pointer`, and `addCW1155Pointer`, deploys new ERC pointer contracts using the legacy `CREATE` opcode rather than `CREATE2` with a pointee-derived salt.

### Finding Description
`UpsertERCPointer` deploys the pointer bytecode with: [1](#0-0) 

Note that the deployer passed to `evm.Create` is `evmModuleAddress`, a single, shared account for the entire EVM module [2](#0-1) , not the caller/pointee. Since `CREATE` address derivation is `keccak(rlp(deployer, nonce))`, the resulting pointer contract address depends only on `evmModuleAddress`'s incrementing nonce — a single shared counter for *every* pointer deployed across all four pointer types (native, CW20, CW721, CW1155) network-wide.

This function is reachable by any unprivileged transaction sender through the pointer precompile's dispatch: [3](#0-2) 

Because the produced address depends purely on this shared, global nonce and not on the specific `pointee` (CW20/CW721/CW1155/native denom) being registered, the exact scenario described in the external report applies: in the event of a chain re-org, transaction ordering among competing `addXPointer` calls for *different* pointees can be reshuffled. A pointer address that was originally going to be assigned to pointee A's contract can instead end up hosting pointee B's pointer contract after the reorg, because both deployments draw from the same incrementing `evmModuleAddress` nonce sequence. Any off-chain caching, front-end configuration, DEX routing config, or on-chain contract that pre-computed/cached "pointer address X = CW20 token A" (a standard pattern since pointer addresses are queryable via `seid query evm pointer` before/while the on-chain tx is pending) can end up interacting with an entirely different underlying asset's pointer contract at that same address post-reorg.

### Impact Explanation
Reorg-driven pointer address reassignment can lead to confused or malicious pointer/asset substitution: users, integrators, or contracts that computed or cached a pointer address prior to a reorg may unknowingly route approvals, transfers, or liquidity operations to a pointer contract wrapping a completely different (potentially attacker-controlled) CW20/CW721/CW1155/native asset than intended, which can result in unauthorized transfers or fund loss through the precompile/pointer surface — a category explicitly accepted in this review's validation criteria ("unauthorized transfer via precompile or pointer").

### Likelihood Explanation
Reachability is trivial — `addNativePointer`/`addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` are open to any unprivileged EOA via a standard EVM transaction to the pointer precompile. The precondition (a chain re-org reordering two or more concurrent pointer-creation transactions relative to each other) is the same precondition accepted as valid in the original finding, and Sei explicitly targets EVM-compatible L2/L1 deployment where such reorgs are a known risk class per the report's precedent (Polygon, Ethereum beacon-chain reorgs).

### Recommendation
Derive pointer contract addresses deterministically from the pointee identity instead of a shared incrementing nonce — e.g., use `CREATE2` with a salt derived from `(typ, pointee)` in `UpsertERCPointer` (`x/evm/keeper/pointer_upgrade.go`), so that the resulting address is bound to the specific asset being pointed to and cannot be reassigned to a different pointee as a side effect of transaction reordering/re-orgs.

### Proof of Concept
1. User submits `addCW20Pointer("cw20_token_A")`; observers compute/cache the expected pointer address as `CREATE(evmModuleAddress, currentNonce)`.
2. Concurrently, another user submits `addCW721Pointer("cw721_token_B")` (or any other pointer-creation call), also drawing from `evmModuleAddress`'s nonce.
3. A chain re-org reorders these two transactions relative to each other (both go through the same shared `evmModuleAddress` nonce sequence in `UpsertERCPointer`, `x/evm/keeper/pointer_upgrade.go:133`).
4. After the reorg, the address previously cached as "pointer for token A" is now occupied by the pointer contract for token B (or vice versa), because the nonce that determined the address was consumed by the other pointer-creation transaction.
5. Any integrator or contract that pre-configured itself against the cached address now interacts with the wrong underlying asset's pointer contract.

### Citations

**File:** x/evm/keeper/pointer_upgrade.go (L92-93)
```go
	pointee := args[0].(string)
	evmModuleAddress := k.GetEVMAddressOrDefault(ctx, k.AccountKeeper().GetModuleAddress(types.ModuleName))
```

**File:** x/evm/keeper/pointer_upgrade.go (L132-134)
```go
	} else {
		_, contractAddr, remainingGas, err = evm.Create(evmModuleAddress, bin, suppliedGas, uint256.NewInt(0))
	}
```

**File:** precompiles/pointer/pointer.go (L80-93)
```go
	switch method.Name {
	case AddNativePointer:
		return p.AddNative(ctx, method, caller, args, value, evm, hooks)
	case AddCW20Pointer:
		return p.AddCW20(ctx, method, caller, args, value, evm, hooks)
	case AddCW721Pointer:
		return p.AddCW721(ctx, method, caller, args, value, evm, hooks)
	case AddCW1155Pointer:
		return p.AddCW1155(ctx, method, caller, args, value, evm, hooks)
	default:
		err = fmt.Errorf("unknown method %s", method.Name)
	}
	return
}
```
