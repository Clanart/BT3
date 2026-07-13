### Title
`originStorage` Cache Not Invalidated After `ExecuteNativeAction` Modifies Same Slot, Causing Stale SLOAD Results During EVM Execution — (`File: x/evm/statedb/state_object.go`)

---

### Summary

`stateObject.GetCommittedState()` caches the first read of a storage slot in `originStorage`. When `ExecuteNativeAction` subsequently modifies the same slot (advancing `s.ctx` to a new cache layer), the `originStorage` cache is never invalidated. Subsequent `GetState()` calls (SLOAD) return the stale pre-action value, while `ForEachStorage` reads from the updated `s.ctx` and returns the new value. The `Commit()` conflict detection only checks `dirtyStorage` keys (EVM-written slots), so this inconsistency goes undetected and the transaction commits silently with the EVM having operated on stale data.

---

### Finding Description

**Root cause — `state_object.go` `GetCommittedState`:**

```go
func (s *stateObject) GetCommittedState(key common.Hash) common.Hash {
    // ...
    if value, cached := s.originStorage[key]; cached {
        return value   // ← always returns the first-read value, never re-checked
    }
    value := s.db.keeper.GetState(s.db.ctx, s.Address(), key)
    s.originStorage[key] = value
    return value
}
``` [1](#0-0) 

`originStorage` is populated on the first `GetState`/`GetCommittedState` call for a key and is **never invalidated** for the lifetime of the `stateObject`.

**`ExecuteNativeAction` advances `s.ctx` to a new cache layer:**

```go
s.cacheMS = nextStore
s.cacheLayers = append(s.cacheLayers, nextStore)
s.ctx = s.ctx.WithMultiStore(nextStore)   // ← s.ctx now sees the native write
``` [2](#0-1) 

After this, `keeper.GetState(s.db.ctx, ...)` would return the updated value `V_new` for slot K — but `GetCommittedState` never reaches that call because `originStorage[K]` is already populated with `V_old`.

**`ForEachStorage` reads from the updated `s.ctx` directly:**

```go
s.keeper.ForEachStorage(s.ctx, addr, func(key, value common.Hash) bool {
    if value, dirty := so.dirtyStorage[key]; dirty {
        return cb(key, value)
    }
    if len(value) > 0 {
        return cb(key, value)   // ← returns V_new from updated s.ctx
    }
    return true
})
``` [3](#0-2) 

`ForEachStorage` does not consult `originStorage` at all; it reads from the KVStore iterator backed by the current `s.ctx`. After a native action, this returns `V_new` for K, while `GetState(K)` returns `V_old`.

**`Commit()` conflict detection is blind to read-only slots:**

```go
for _, key := range obj.dirtyStorage.SortedKeys() {   // ← only EVM-written keys
    origin := obj.originStorage[key]
    dirty  := obj.dirtyStorage[key]
    if dirty == origin { continue }
    store := s.keeper.GetState(s.ctx, obj.Address(), key)
    if store != origin && store != dirty {
        return fmt.Errorf("%w: ...", ErrStateConflict, ...)
    }
}
``` [4](#0-3) 

If the EVM only **read** slot K (never wrote it), K is absent from `dirtyStorage`. The conflict check is skipped entirely. The native action's write to K is committed via `flushNativeCacheLayers()`, but the EVM operated on `V_old` throughout — with no error raised.

---

### Impact Explanation

Within a single EVM transaction, two different values for the same storage key are simultaneously accessible:

| Access path | Value returned |
|---|---|
| `GetState(K)` / SLOAD | `V_old` (stale `originStorage` cache) |
| `ForEachStorage` / `GetStorageRoot` | `V_new` (live `s.ctx` after native action) |

An EVM contract that:
1. Reads slot K (SLOAD → `V_old` cached),
2. Calls a precompile that triggers `ExecuteNativeAction` modifying K to `V_new`,
3. Reads K again via SLOAD → still receives `V_old`,

will execute its financial logic on stale data. If K encodes a balance, allowance, or access-control flag, the contract's decision (e.g., "is there enough balance to withdraw?") is based on the pre-action value. The committed state has `V_new`, but the EVM's execution path used `V_old` — a silent mis-accounting that `Commit()` never flags.

This matches: **High — EVM state transition bug that permits valid user funds/fees to be mis-accounted.**

---

### Likelihood Explanation

The trigger requires:
1. A contract that reads a storage slot K before calling a precompile.
2. The precompile uses `ExecuteNativeAction` to write to K (e.g., a bank/staking precompile that updates a slot tracking a mirrored balance or allowance).
3. The contract reads K again after the precompile returns.

This is a realistic pattern for any contract that mirrors native (Cosmos) state into EVM storage and interacts with precompiles that update that state. The attacker controls the transaction and contract logic; no privileged role is required.

---

### Recommendation

After each successful `ExecuteNativeAction`, invalidate all `originStorage` entries for the contract address whose storage was modified by the native action. Concretely, for every `stateObject` whose address matches the contract touched by the native action, clear the corresponding `originStorage` entries so that the next `GetCommittedState` call re-reads from the now-current `s.ctx`:

```go
// After s.ctx = s.ctx.WithMultiStore(nextStore):
if obj, ok := s.stateObjects[contract]; ok {
    obj.originStorage = make(Storage)
}
```

Alternatively, track a "native-write set" per `ExecuteNativeAction` call and selectively evict only the affected keys from `originStorage`, mirroring the remediation in the referenced report (tracking all encountered values per key to detect multi-value access within a transaction).

---

### Proof of Concept

```
Initial state: contract C, slot K = V_old (committed to store)

Step 1: EVM transaction begins.
        stateDB.GetState(C, K)
        → stateObject.GetCommittedState(K)
        → originStorage[K] = V_old  (cache populated)
        → returns V_old ✓

Step 2: EVM calls precompile P which invokes ExecuteNativeAction:
        action writes K = V_new via innerCtx
        innerDB.Commit() → keeper.SetState(innerCtx, C, K, V_new)
        ExecuteNativeAction succeeds:
          s.ctx = s.ctx.WithMultiStore(nextStore)  // nextStore has K=V_new

Step 3: EVM calls stateDB.GetState(C, K) again (SLOAD):
        → stateObject.GetState(K)
        → K not in dirtyStorage
        → GetCommittedState(K)
        → originStorage[K] = V_old  ← HIT: returns V_old (STALE)
        Contract logic executes based on V_old ✗

Step 4: stateDB.ForEachStorage(C, cb):
        → keeper.ForEachStorage(s.ctx, C, ...)
        → iterator reads from nextStore → K = V_new
        → K not in dirtyStorage → cb(K, V_new) ← returns V_new ✗

        Two different values for K within the same transaction:
          GetState → V_old
          ForEachStorage → V_new

Step 5: stateDB.Commit():
        K not in dirtyStorage → conflict check skipped
        flushNativeCacheLayers() → K = V_new committed
        No error returned.

Result: EVM executed with K = V_old; committed state has K = V_new.
        Silent mis-accounting; no ErrStateConflict raised.
``` [1](#0-0) [5](#0-4) [6](#0-5) [3](#0-2)

### Citations

**File:** x/evm/statedb/state_object.go (L212-228)
```go
// GetCommittedState query the committed state
func (s *stateObject) GetCommittedState(key common.Hash) common.Hash {
	if s.overrideStorage != nil {
		if value, ok := s.overrideStorage[key]; ok {
			return value
		}
		return common.Hash{}
	}

	if value, cached := s.originStorage[key]; cached {
		return value
	}
	// If no live objects are available, load it from keeper
	value := s.db.keeper.GetState(s.db.ctx, s.Address(), key)
	s.originStorage[key] = value
	return value
}
```

**File:** x/evm/statedb/statedb.go (L348-364)
```go
// ForEachStorage iterate the contract storage, the iteration order is not defined.
func (s *StateDB) ForEachStorage(addr common.Address, cb func(key, value common.Hash) bool) error {
	so := s.getStateObject(addr)
	if so == nil {
		return nil
	}
	s.keeper.ForEachStorage(s.ctx, addr, func(key, value common.Hash) bool {
		if value, dirty := so.dirtyStorage[key]; dirty {
			return cb(key, value)
		}
		if len(value) > 0 {
			return cb(key, value)
		}
		return true
	})
	return nil
}
```

**File:** x/evm/statedb/statedb.go (L373-401)
```go
func (s *StateDB) ExecuteNativeAction(contract common.Address, converter EventConverter, action func(ctx sdk.Context) error) error {
	prevStore := s.cacheMS
	prevLayerCount := len(s.cacheLayers)

	nextStore, ok := s.cacheMS.CacheMultiStore().(cachemulti.Store)
	if !ok {
		panic("expect nested CacheMultiStore result to be cachemulti.Store")
	}

	eventManager := sdk.NewEventManager()
	actionCtx := s.ctx.WithMultiStore(nextStore).WithEventManager(eventManager)

	if err := action(actionCtx); err != nil {
		return err
	}

	s.cacheMS = nextStore
	s.cacheLayers = append(s.cacheLayers, nextStore)
	s.ctx = s.ctx.WithMultiStore(nextStore)

	events := eventManager.Events()
	s.emitNativeEvents(contract, converter, events)
	s.nativeEvents = s.nativeEvents.AppendEvents(events)
	s.journal.append(nativeChange{
		previousStore:      prevStore,
		previousLayerCount: prevLayerCount,
		events:             len(events),
	})
	return nil
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
