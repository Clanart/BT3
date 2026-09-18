### Title
Pointer registration upsert race allows a concurrently-executed transaction to silently overwrite an already-registered CW/native pointer address - ([File: x/evm/keeper/pointer_upgrade.go])

### Summary
`UpsertERCPointer` (used by the public `pointer` precompile's `addNativePointer`/`addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` methods) performs a check-then-act "exists" lookup and a later "set" write against a special *live* multistore context that is deliberately unfrozen and separate from the caller's snapshot `ctx`. Any unprivileged EVM caller can invoke these precompile methods for the same `pointee` (denom / CW20 / CW721 / CW1155 address) from two different transactions in the same block. Under the OCC/multiversion parallel execution path, the get/exists check and the later set on the pointer key are not tied to the ordinary read-set/write-set conflict machinery used elsewhere in the codebase; this is the exact bug class from the Maia report: a stale "not yet registered" check that a second concurrent request can pass, followed by an unconditional overwrite of the mapping key, silently invalidating a value that already existed.

### Finding Description
`AddNative`, `AddCW20`, `AddCW721`, and `AddCW1155` in `precompiles/pointer/pointer.go` are public, unprivileged EVM entry points (any address can call them, subject only to `readOnly`/delegatecall checks) that delegate to `UpsertERC*Pointer` → `UpsertERCPointer`. [1](#0-0) 

`UpsertERCPointer` reads the current pointer for `pointee` via `getter(liveCtx())`, then later deploys/points and calls `setter(liveCtx(), pointee, contractAddr)`. The comment in the code itself documents that this deliberately bypasses the normal frozen `ctx` in favor of a "live unfrozen top" state layer taken from the EVM `StateDB`, specifically to work around store-freezing semantics introduced by `evm.Create`/`GetDeploymentCode` snapshotting: [2](#0-1) 

This is precisely the pattern flagged in the Maia finding: a "check-then-act" existence check (`existingAddr, _, exists := getter(...)`) whose result is trusted to still be valid at the time of the later unconditional `setter(...)` write, with no atomic guard tying the read to the write on the same version of state. If two transactions register a pointer for the same `pointee` concurrently (e.g. under `giga`'s OCC/Block-STM speculative execution path, which explicitly tracks conflicts only via `(address, slot)` storage/balance/nonce/code access keys, not custom-precompile keeper state — documented as future/"open" work), both can observe `exists = false` (or stale `existingAddr`), both deploy contracts, and the last `setter` call to commit wins, overwriting the mapping that the other transaction's contract address had just set. [3](#0-2) 

### Impact Explanation
If a second pointer-registration transaction races ahead and overwrites the pointer mapping recorded by a first, the first pointer contract (already deployed on-chain, and potentially already used by end users to wrap/hold the underlying native denom or CW20/CW721/CW1155 asset through the ERC20/ERC721/ERC1155 pointer semantics) is silently orphaned from the canonical `GetERC*Pointer` lookup used by every other precompile and by `pointerview`. Any balances or references built against the now-unlinked pointer address become unreachable through the canonical routing path, resulting in fund loss/permanent freezing for users who interacted with the first pointer before the overwrite, matching the accepted impact class of "concrete fund loss or permanent freezing."

### Likelihood Explanation
Likelihood is moderate: it requires two independent (or same-actor, self-races are also possible) `addNativePointer`/`addCW20Pointer` invocations for the same `pointee` to be included and speculatively executed within the OCC parallel path in the same block window, and requires that the custom-precompile keeper state accesses are indeed not participating in the same read/write conflict detection as ordinary EVM state (which the codebase's own documentation flags as unresolved/"open" design work for custom precompiles under the `giga` OCC executor). Because pointer registration is a public, gas-cheap, idempotent-looking operation with no access control, any user (or the same user submitting duplicate requests) can trigger the race deliberately.

### Recommendation
Ensure the pointer existence check and the pointer address write happen against the same state version used for OCC conflict tracking (i.e., make the custom precompile's keeper reads/writes visible to the executor's read-set/write-set, consistent with the stated design direction of treating precompile-migrated state as ordinary `(address, slot)` storage). Alternatively, make `UpsertERCPointer`'s exists-check-then-write atomic with respect to the same key, e.g. by re-validating existence immediately before the commit-time write, or by having the storage layer itself reject/serialize concurrent writers to the same `pointee` key rather than trusting a possibly-stale `exists` result read earlier in execution.

### Proof of Concept
Not independently reproducible from indexed context alone — a concrete PoC would require driving `giga`'s OCC executor with two transactions calling `addNativePointer`/`addCW20Pointer` for the same `pointee` in the same block and confirming that the custom-precompile keeper's `GetERC*Pointer`/`SetERC*Pointer` access is not tracked in the executor's `stateAccessIndex`, causing both incarnations to pass validation and the later one to overwrite the earlier pointer mapping. This would need to be validated with a background Devin session that can run the `giga/evmonly` OCC test harness against `x/evm/keeper/pointer_upgrade.go`'s `UpsertERCPointer`.

### Citations

**File:** precompiles/pointer/pointer.go (L99-132)
```go
func (p PrecompileExecutor) AddNative(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	token := args[0].(string)
	metadata, metadataExists := p.bankKeeper.GetDenomMetaData(ctx, token)
	if !metadataExists {
		return nil, 0, fmt.Errorf("denom %s does not have metadata stored and thus can only have its pointer set through gov proposal", token)
	}
	name := metadata.Name
	symbol := metadata.Symbol
	var decimals uint8
	for _, denomUnit := range metadata.DenomUnits {
		if denomUnit.Exponent > uint32(decimals) && denomUnit.Exponent <= math.MaxUint8 {
			decimals = uint8(denomUnit.Exponent)
			name = denomUnit.Denom
			symbol = denomUnit.Denom
			if len(denomUnit.Aliases) > 0 {
				name = denomUnit.Aliases[0]
			}
		}
	}
	contractAddr, err := p.evmKeeper.UpsertERCNativePointer(ctx, evm, token, utils.ERCMetadata{Name: name, Symbol: symbol, Decimals: decimals})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** x/evm/keeper/pointer_upgrade.go (L89-141)
```go
func (k *Keeper) UpsertERCPointer(
	ctx sdk.Context, evm *vm.EVM, typ string, args []interface{}, getter PointerGetter, setter PointerSetter,
) (contractAddr common.Address, err error) {
	pointee := args[0].(string)
	evmModuleAddress := k.GetEVMAddressOrDefault(ctx, k.AccountKeeper().GetModuleAddress(types.ModuleName))

	var bin []byte
	bin, err = artifacts.GetParsedABI(typ).Pack("", args...)
	if err != nil {
		panic(err)
	}
	bin = append(artifacts.GetBin(typ), bin...)
	// GetDeploymentCode / Create take EVM snapshots that Freeze() Multistore layers.
	// Exists-lookup and commits must use the live unfrozen top (sdb.Ctx): cachekv
	// forbids writing a frozen layer, and same-tx readers that skip frozen-empty
	// parents would miss those writes. The precompile Prepare `ctx` is that top at
	// Prepare time, but is frozen once this Upsert snapshots. Always attach the
	// caller's gas meter (finite precompile meter in deliver) — sdb.Ctx() alone
	// carries the infinite EVM meter.
	sdb := state.GetDBImpl(evm.StateDB)
	liveCtx := func() sdk.Context {
		if sdb == nil {
			return ctx
		}
		return sdb.Ctx().WithGasMeter(ctx.GasMeter())
	}
	existingAddr, _, exists := getter(liveCtx(), pointee)
	suppliedGas := k.getEvmGasLimitFromCtx(ctx)
	var remainingGas uint64
	if exists {
		var ret []byte
		contractAddr = existingAddr
		ret, remainingGas, err = evm.GetDeploymentCode(evmModuleAddress, bin, suppliedGas, utils.Big0, existingAddr)
		if err != nil {
			return
		}
		// Only write on success: a failed GetDeploymentCode can leave ret as nil or
		// revert data, which must not clobber live pointer bytecode (even transiently).
		writeCtx := liveCtx()
		k.SetCode(writeCtx, contractAddr, ret)
		if sdb != nil {
			sdb.RefreshCodeCache(contractAddr, ret)
		}
	} else {
		_, contractAddr, remainingGas, err = evm.Create(evmModuleAddress, bin, suppliedGas, uint256.NewInt(0))
	}
	if err != nil {
		return
	}
	ctx.GasMeter().ConsumeGas(k.GetCosmosGasLimitFromEVMGas(ctx, suppliedGas-remainingGas), "ERC pointer deployment")
	if err = setter(liveCtx(), pointee, contractAddr); err != nil {
		return
	}
```

**File:** giga/evmonly/README.md (L179-193)
```markdown
## Open precompile work

Native custom precompiles still need a separate design. If they introduce state
outside balance, nonce, code, and storage, that state must either become part of
the EVM-native changeset or be represented through an explicit extension that is
visible to the OCC conflict tracker.

The intended direction is to treat each custom precompile's migrated module
state as contract storage owned by that precompile address. With no range reads
and no side state, precompile reads and writes can then flow through ordinary
`(address, slot)` storage tracking.

Until that design is implemented, the `evmonly` executor accepts a custom
precompile registry only as a fail-closed placeholder. Calls to registered
custom precompile addresses return `ErrCustomPrecompilesOpen`.
```
