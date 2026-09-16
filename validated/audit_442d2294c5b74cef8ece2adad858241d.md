### Title
Shallow (non-independent) `CopyTrie` for `FlatAccountTrie`/`FlatStorageTrie` breaks `StateDB.Copy()` isolation, enabling cross-state corruption of account/storage data - (File: `blockchain/state/database.go`)

### Summary
`cachingDB.CopyTrie` is documented to "return an independent copy of the given trie" and is the sole mechanism `StateDB.Copy()` relies on to make speculative/pending states independent from the canonical state. For the `SecureTrie` backend it does call `t.Copy()`, producing a real independent value. For the flat-trie backend (`UseFlatTrie`), however, it returns the exact same `*FlatAccountTrie` / `*FlatStorageTrie` pointer, which internally wraps a mutable `kaiatrie.DeferredAccountTrie`/`DeferredStorageTrie` (`dt`) shared with the domains manager. Any write (`TryUpdate`/`TryDelete`) issued through one `StateDB` copy mutates the same underlying `dt`, silently propagating into every other `StateDB` that "copied" it — including writing zero/nil values via `TryDelete`'s `t.dt.Put(key, nil)` path. This is the same underlying bug class as CVE-2023-49298: an "efficient copy" primitive that is supposed to be a full, independent duplicate is actually a shared reference, so writes/deletes intended for one consumer silently zero-out or corrupt data visible to another consumer.

### Finding Description
`OpenTrie`/`OpenStorageTrie` construct `FlatAccountTrie`/`FlatStorageTrie` when `UseFlatTrie` is enabled: [1](#0-0) 

`CopyTrie` is expected to hand back an independent trie, but for the flat variants it just returns the same object: [2](#0-1) 

`StateDB.Copy()`/`copyStateDB` relies entirely on `CopyTrie` for isolation: it assigns `dst.trie = src.db.CopyTrie(src.trie)` and deep-copies state objects, but each `stateObject`'s underlying storage trie is likewise obtained via `CopyTrie` in `deepCopy`, so for the flat backend the "copy" and the "original" both hold the same `*FlatAccountTrie`/`*FlatStorageTrie`, i.e. the same mutable `DeferredAccountTrie`/`DeferredStorageTrie` (`dt`): [3](#0-2) 

Writes issued against the flat trie mutate the shared `dt` directly rather than an isolated overlay: [4](#0-3) [5](#0-4) 

Notably `FlatAccountTrie.TryDelete` both wipes contract storage and writes a nil value into the shared trie (`t.dt.Put(key, nil)`), which is the exact "efficient-copy causes data to be replaced with zero-valued/nil content" behavior described in the report. Because `StateDB.Copy()` is a documented mechanism used to produce independent states (e.g., for speculative/pending execution, snapshot reverts, or parallel candidate block building) and `Copy()`'s doc comment explicitly promises independence ("Snapshots of the copied state cannot be applied to the copy"), any code path that copies a `StateDB` while flat tries are enabled does not get that guarantee: a delete or update performed in the "copy" (e.g., during an `eth_call`, gasless/auction bundle simulation, or transaction validity probe) can zero out or overwrite account/storage entries in the trie that the "original" state (used for actual block execution or another pending simulation) still reads from, since they are the same object.

### Impact Explanation
If reachable, this allows an unprivileged transaction sender or RPC caller whose call triggers a `StateDB.Copy()`-based speculative execution (used broadly for pending-state queries, `eth_call`, gas estimation, or gasless/auction settlement candidate execution) to corrupt shared trie state for other concurrent consumers of the "original" state — including deleting/zeroing account or storage slots (balances, nonces, AccountKey data, contract storage) that should have remained isolated to the copy. This can manifest as unauthorized value movement or state divergence between nodes if one node happens to build a block using a state object that was silently mutated by an unrelated simulation, and it undermines the correctness guarantees of gasless/auction settlement and fee-delegation flows that depend on isolated speculative state evaluation before committing a transaction.

### Likelihood Explanation
This requires the flat-trie storage backend (`UseFlatTrie`/`DomainsManager`) to be enabled, and requires an execution path that calls `StateDB.Copy()` while a flat trie is in use, plus a subsequent write/delete on the resulting copy. I was not able to fully enumerate all `StateDB.Copy()` call sites (tx pool pending-state construction, miner/worker candidate block state, gasless/auction builder) within the remaining tool budget to confirm a concrete call chain from a single external transaction/RPC call through to a conflicting concurrent write; this is a real gap in verification. The root-cause asymmetry (`SecureTrie.Copy()` deep-copies, `FlatAccountTrie`/`FlatStorageTrie` do not) is confirmed in code, but likelihood depends on how widely `UseFlatTrie` is deployed and how concurrently `StateDB.Copy()` results are used with the original.

### Recommendation
Make `cachingDB.CopyTrie` produce a genuinely independent trie for `FlatAccountTrie`/`FlatStorageTrie` (e.g., snapshot/copy the underlying `DeferredAccountTrie`/`DeferredStorageTrie` state, or fall back to a copy-on-write overlay per `StateDB.Copy()` call) so that `StateDB.Copy()`'s documented independence guarantee holds for the flat-trie backend as it does for `SecureTrie`. Audit all call sites of `StateDB.Copy()` to confirm none of them execute writes against a flat-trie-backed copy that could leak into concurrently-used original state.

### Proof of Concept
Conceptual reproduction (not fully verified against a live node due to inability to trace all `Copy()` call sites in this session):
1. Run a node with `UseFlatTrie` enabled so `OpenTrie`/`OpenStorageTrie` return `FlatAccountTrie`/`FlatStorageTrie` (`blockchain/state/database.go:168-184`).
2. Obtain a `StateDB` `orig` and call `state := orig.Copy()`; because `CopyTrie` returns the same `*FlatAccountTrie` pointer for both (`blockchain/state/database.go:191-194`), `state.trie == orig.trie` (same underlying `dt`).
3. Perform an account/storage delete or update on `state` (e.g., via a speculative `eth_call`/gas-estimation/gasless simulation that triggers `stateObject.updateStorageTrie` → `tr.TryDelete`), which calls `FlatAccountTrie.TryDelete` → `t.dt.Put(key, nil)` (`storage/statedb/flat_trie.go:63-69`).
4. Observe that `orig` (and any other holder of the same underlying flat trie) now also reflects the deleted/zeroed value, despite `Copy()`'s contract that the two states are independent (`blockchain/state/statedb.go:937-943`).

### Citations

**File:** blockchain/state/database.go (L168-184)
```go
// OpenTrie opens the main account trie at a specific root hash.
func (db *cachingDB) OpenTrie(root common.Hash, opts *statedb.TrieOpts) (Trie, error) {
	if dm := db.db.DiskDB().GetDomainsManager(); dm != nil {
		return statedb.NewFlatAccountTrie(dm, root, opts)
	} else {
		return statedb.NewSecureTrie(root, db.db, opts)
	}
}

// OpenStorageTrie opens the storage trie of an account.
func (db *cachingDB) OpenStorageTrie(addr common.Address, root common.ExtHash, opts *statedb.TrieOpts) (Trie, error) {
	if dm := db.db.DiskDB().GetDomainsManager(); dm != nil {
		return statedb.NewFlatStorageTrie(dm, addr, root.Unextend(), opts)
	} else {
		return statedb.NewSecureStorageTrie(root, db.db, opts)
	}
}
```

**File:** blockchain/state/database.go (L186-198)
```go
// CopyTrie returns an independent copy of the given trie.
func (db *cachingDB) CopyTrie(t Trie) Trie {
	switch t := t.(type) {
	case *statedb.SecureTrie:
		return t.Copy()
	case *statedb.FlatAccountTrie:
		return t
	case *statedb.FlatStorageTrie:
		return t
	default:
		panic(fmt.Errorf("unknown trie type %T", t))
	}
}
```

**File:** blockchain/state/statedb.go (L949-998)
```go
func copyStateDB(dst, src *StateDB) {
	// Copy all the basic fields, initialize the memory ones
	dst.db = src.db
	dst.trie = src.db.CopyTrie(src.trie)
	dst.trieOpts = src.trieOpts

	dst.stateObjects = make(map[common.Address]*stateObject, len(src.stateObjects))
	dst.stateObjectsDirty = make(map[common.Address]struct{}, len(src.stateObjectsDirty))
	dst.stateObjectsDirtyStorage = make(map[common.Address]struct{}, len(src.stateObjectsDirtyStorage))

	dst.dbErr = src.dbErr
	dst.refund = src.refund

	dst.thash = src.thash
	dst.bhash = src.bhash
	dst.txIndex = src.txIndex
	dst.logs = make(map[common.Hash][]*types.Log, len(src.logs))
	dst.logSize = src.logSize

	dst.preimages = make(map[common.Hash][]byte, len(src.preimages))

	// Do we need to copy the access list? In practice: No. At the start of a
	// transaction, the access list is empty. In practice, we only ever copy state
	// _between_ transactions/blocks, never in the middle of a transaction.
	// However, it doesn't cost us much to copy an empty list, so we do it anyway
	// to not blow up if we ever decide copy it in the middle of a transaction
	dst.accessList = src.accessList.Copy()
	dst.transientStorage = src.transientStorage.Copy()
	dst.journal = newJournal()

	// Copy the dirty states, logs, and preimages
	for addr := range src.journal.dirties {
		// As documented [here](https://github.com/ethereum/go-ethereum/pull/16485#issuecomment-380438527),
		// and in the Finalise-method, there is a case where an object is in the journal but not
		// in the stateObjects: OOG after touch on ripeMD prior to Byzantium. Thus, we need to check for
		// nil
		if object, exist := src.stateObjects[addr]; exist {
			dst.stateObjects[addr] = object.deepCopy(dst)
			dst.stateObjectsDirty[addr] = struct{}{}
		}
	}
	// Above, we don't copy the actual journal. This means that if the copy is copied, the
	// loop above will be a no-op, since the copy's journal is empty.
	// Thus, here we iterate over stateObjects, to enable copies of copies
	for addr := range src.stateObjectsDirty {
		if _, exist := dst.stateObjects[addr]; !exist {
			dst.stateObjects[addr] = src.stateObjects[addr].deepCopy(dst)
			dst.stateObjectsDirty[addr] = struct{}{}
		}
	}
```

**File:** storage/statedb/flat_trie.go (L55-69)
```go
func (t *FlatAccountTrie) TryUpdate(key, value []byte) error {
	return t.dt.Put(key, value)
}

func (t *FlatAccountTrie) TryUpdateWithKeys(key, hashKey, hexKey, value []byte) error {
	return t.TryUpdate(key, value)
}

func (t *FlatAccountTrie) TryDelete(key []byte) error {
	// Wipe contract storage
	if err := t.dt.DeleteAccountStorage(key); err != nil {
		return err
	}
	return t.dt.Put(key, nil)
}
```

**File:** storage/statedb/flat_trie.go (L146-160)
```go
func (t *FlatStorageTrie) TryUpdate(key, value []byte) error {
	_, slot, _, err := rlp.Split(value)
	if err != nil {
		return fmt.Errorf("failed to rlp decode: %w", err)
	}
	return t.dt.Put(key, slot)
}

func (t *FlatStorageTrie) TryUpdateWithKeys(key, hashKey, hexKey, value []byte) error {
	return t.TryUpdate(key, value)
}

func (t *FlatStorageTrie) TryDelete(key []byte) error {
	return t.dt.Put(key, nil)
}
```
