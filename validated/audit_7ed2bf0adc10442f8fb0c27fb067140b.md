Based on my investigation, I found a genuine analog matching the TOCTOU bug class in the CW↔EVM pointer-upgrade path.

### Title
Check-then-act pointer existence race in `UpsertERCPointer` bypasses OCC readset tracking, enabling pointer overwrite/loss under parallel execution - (File: `x/evm/keeper/pointer_upgrade.go`)

### Summary
`UpsertERCPointer`, used by the CW→ERC pointer precompiles (`AddNative`, `AddCW20`, `AddCW721`, `AddCW1155`), performs a check-then-act sequence: it looks up whether a pointer already exists, and based on that check either creates a brand-new pointer contract or patches the bytecode of the existing one. The existence check is deliberately performed against a *different* context (`sdb.Ctx()`, the "live unfrozen top") than the one the EVM snapshot/`Create` machinery uses, per the comment at [1](#0-0) .

### Finding Description
`UpsertERCPointer` reads pointer existence via `getter(liveCtx())` and, only afterward, deploys code via `evm.Create` (new pointer) or `evm.GetDeploymentCode` + `k.SetCode` (existing pointer), before finally calling `setter(liveCtx(), pointee, contractAddr)`: [2](#0-1) 

This is structurally the same "check for missing state, then act on that stale check" pattern as the CVE: the exists-check and the eventual write are decoupled and performed against different underlying store contexts (comment explicitly acknowledges the check must bypass the "frozen" EVM-snapshot context to see live data — i.e., the check is not tied to the same conflict-tracking context that the transaction's write path uses). If this getter/setter path is exercised inside OCC's optimistic parallel execution (`sei-cosmos/tasks/scheduler.go`, `sei-cosmos/store/multiversion/store.go`), a genuine TOCTOU can occur: OCC's conflict detection depends entirely on all reads flowing through the tracked readset so that `ValidateReadset`/`checkReadsetAtIndex` can invalidate a transaction whose exists-check became stale, as documented in [3](#0-2)  and [4](#0-3) . If the pointer exists-check is read through a context/path that is not registered in that transaction's readset (as the "live unfrozen top" comment implies is necessary here to avoid writing into a frozen cachekv layer), two concurrently-scheduled transactions creating a pointer for the same `pointee` could both observe `exists=false`, both call `evm.Create`, and the second `setter` call would silently overwrite the first pointer registration — with no OCC abort/re-run being triggered, because the untracked read never entered the conflict-detection machinery.

### Impact Explanation
If two transactions register/upsert a pointer for the same CW20/CW721/CW1155/native token concurrently (via the pointer precompile's `AddCW20`/`AddCW721`/`AddCW1155`/`AddNative` methods, reachable by any EVM caller), the overwrite of `SetERC20CW20Pointer`/`SetERC721CW721Pointer`/etc. can silently point token holders and integrators at an unexpected or freshly-redeployed contract address, effectively orphaning balances/approvals recorded against the previously created pointer contract. That is a form of state corruption/fund-inaccessibility for anyone who had already interacted with the discarded pointer contract, and it is triggered purely by ordinary transaction submission (no privileged access needed).

### Likelihood Explanation
This requires the pointer upsert path to actually execute under Sei's optimistic parallel execution schedulers (`sei-cosmos/tasks/scheduler.go`, `giga/evmonly/occ.go`) with two conflicting pointer-creation transactions in the same block, and for the exists-check inside `UpsertERCPointer` to indeed be outside the tracked readset for that transaction incarnation. I was not able to fully confirm from static reading alone that `sdb.Ctx()` bypasses the `VersionIndexedStore` readset tracking in all execution modes (V2 OCC vs. Giga/evmonly OCC) — this needs to be validated by a background agent with the ability to trace `state.GetDBImpl`/`DBImpl.Ctx()` back to whichever multi-store wrapper is active during OCC execution, and to write a concurrency test reproducing two same-block `AddCW20` calls for the same `cwAddr`.

### Recommendation
Ensure the existence check and the final `setter` write in `UpsertERCPointer` are performed through the exact same store context that is subject to OCC readset/writeset tracking for that transaction (i.e., do not read pointer existence from an "unfrozen" bypass context that isn't the one whose reads get validated). If the "frozen multistore layer" constraint genuinely requires reading current state, the read must still be recorded into the same readset used for OCC conflict detection, or the whole check-then-write sequence should be wrapped so it is atomically committed/validated as a unit (e.g., re-validate existence immediately before the final `setter` call using the same conflict-tracked context, and abort/retry on mismatch).

### Proof of Concept
Not independently reproduced; a concrete PoC would submit two transactions in the same block that both call the pointer precompile's `Add*` entrypoint for the same `pointee` address, executed under `GigaOCCEnabled`/`ProcessTXsWithOCCGiga` or `ProcessTXsWithOCCV2` (`app/app.go` lines ~1556-1622), and assert that the second call's `SetCode`/`Create` silently overwrites the first without an OCC abort — this would need to be built and run by a background agent with repository execution access.

### Citations

**File:** x/evm/keeper/pointer_upgrade.go (L101-141)
```go
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

**File:** sei-cosmos/store/multiversion/mvkv.go (L184-236)
```go
// This function iterates over the readset, validating that the values in the readset are consistent with the values in the multiversion store and underlying parent store, and returns a boolean indicating validity
func (store *VersionIndexedStore) ValidateReadset() bool {
	// TODO: remove?
	// store.mtx.Lock()
	// defer store.mtx.Unlock()
	// defer telemetry.MeasureSince(time.Now(), "store", "mvkv", "validate_readset")

	// sort the readset keys - this is so we have consistent behavior when theres varying conflicts within the readset (eg. read conflict vs estimate)
	readsetKeys := make([]string, 0, len(store.readset))
	for key := range store.readset {
		readsetKeys = append(readsetKeys, key)
	}
	sort.Strings(readsetKeys)

	// iterate over readset keys and values
	for _, strKey := range readsetKeys {
		key := []byte(strKey)
		valueArr := store.readset[strKey]
		if len(valueArr) != 1 {
			// if we have more than one value, we will fail the validation since we dedup when adding to readset
			return false
		}
		value := valueArr[0]
		mvsValue := store.multiVersionStore.GetLatestBeforeIndex(store.transactionIndex, key)
		if mvsValue != nil {
			if mvsValue.IsEstimate() {
				// if we see an estimate, that means that we need to abort and rerun
				store.WriteAbort(scheduler.NewEstimateAbort(mvsValue.Index()))
				return false
			} else {
				if mvsValue.IsDeleted() {
					// check for `nil`
					if value != nil {
						return false
					}
				} else {
					// check for equality
					if string(value) != string(mvsValue.Value()) {
						return false
					}
				}
			}
			continue // value is valid, continue to next key
		}

		parentValue := store.parent.Get(key)
		if string(parentValue) != string(value) {
			// this shouldnt happen because if we have a conflict it should always happen within multiversion store
			panic("we shouldn't ever have a readset conflict in parent store")
		}
		// value was correct, we can continue to the next value
	}
	return true
```

**File:** sei-cosmos/store/multiversion/store.go (L392-446)
```go
func (s *Store) checkReadsetAtIndex(index int) (bool, []int, []string) {
	conflictSet := make(map[int]struct{})
	var conflictKeys []string
	valid := true

	readSetAny, found := s.txReadSets.Load(index)
	if !found {
		return true, []int{}, nil
	}
	readset := readSetAny.(ReadSet)
	// iterate over readset and check if the value is the same as the latest value relateive to txIndex in the multiversion store
	for key, valueArr := range readset {
		if len(valueArr) != 1 {
			valid = false
			continue
		}
		value := valueArr[0]
		// get the latest value from the multiversion store
		latestValue := s.GetLatestBeforeIndex(index, []byte(key))
		if latestValue == nil {
			// this is possible if we previously read a value from a transaction write that was later reverted, so this time we read from parent store
			parentVal := s.parentStore.Get([]byte(key))
			if !bytes.Equal(parentVal, value) {
				valid = false
				conflictKeys = append(conflictKeys, key)
			}
		} else {
			// if estimate, mark as conflict index - but don't invalidate
			if latestValue.IsEstimate() {
				conflictSet[latestValue.Index()] = struct{}{}
			} else if latestValue.IsDeleted() {
				if value != nil {
					// conflict
					// TODO: would we want to return early?
					conflictSet[latestValue.Index()] = struct{}{}
					valid = false
					conflictKeys = append(conflictKeys, key)
				}
			} else if !bytes.Equal(latestValue.Value(), value) {
				conflictSet[latestValue.Index()] = struct{}{}
				valid = false
				conflictKeys = append(conflictKeys, key)
			}
		}
	}

	conflictIndices := make([]int, 0, len(conflictSet))
	for index := range conflictSet {
		conflictIndices = append(conflictIndices, index)
	}

	sort.Ints(conflictIndices)

	return valid, conflictIndices, conflictKeys
}
```
