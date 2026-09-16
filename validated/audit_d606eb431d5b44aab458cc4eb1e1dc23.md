### Title
Unsynchronized read/write race on `ValsetModule.validatorVoteBlockNumsCache` / `lowestScannedVoteNumCache` can yield stale or torn validator-council data, causing consensus state divergence - (File: kaiax/valset/impl/getter_council.go)

### Summary
`ValsetModule` caches the migration bookkeeping fields `lowestScannedVoteNumCache` (`*uint64`) and `validatorVoteBlockNumsCache` (`[]uint64`) as plain, unlocked struct fields. They are written from the block-processing path (`applyBlock`) and read from the council/proposer getter path (`getCouncilDB`, `getValidIstanbulSnapshotBefore`, `scanBlocks`, `readLowestScannedVoteNumCached`, `readValidatorVoteBlockNumsCached`), both of which are reachable concurrently — the write side runs during block insertion/execution, the read side is invoked by public governance/validator RPC queries and by proposer/council computation for arbitrary historical or upcoming block numbers. Neither side takes a mutex, mirroring the CVE-2025-21915 pattern where `driver_override_show()` read a mutable pointer/slice field concurrently with `driver_override_store()` mutating/freeing it under lock, without the reader holding the same lock.

### Finding Description
`readLowestScannedVoteNumCached` and `readValidatorVoteBlockNumsCached` lazily populate and return the cached fields without any synchronization: [1](#0-0) 

These caches are invalidated/rewritten from `applyBlock`, which is invoked while applying validator votes during block processing: [2](#0-1) 

The same unlocked cache is consumed from `getCouncilDB` and `getValidIstanbulSnapshotBefore` to decide whether historical council data comes from the DB or from the legacy Istanbul snapshot fallback path: [3](#0-2) [4](#0-3) 

It is also read in `getter_proposers.go`'s `scanBlocks`, which determines which blocks are scanned for remove-validator votes when computing the proposer list for a given interval: [5](#0-4) 

Because `v.validatorVoteBlockNumsCache` is a `[]uint64` slice (a 3-word header: pointer, length, capacity) and `v.lowestScannedVoteNumCache` is a `*uint64`, a concurrent writer clearing the field (`v.validatorVoteBlockNumsCache = nil` in `applyBlock`) while a reader is mid-read in `readValidatorVoteBlockNumsCached`/`lastNumLessThan`/`scanBlocks` can observe a torn/partial slice header (in the absence of the Go race detector catching it first) or, more reliably in practice, observe **stale** cache contents that do not yet reflect the votes just applied by the writer goroutine. This directly changes which branch `getCouncilDB` takes (DB-backed vs. legacy Istanbul-snapshot fallback) and which blocks `scanBlocks` includes when computing remove-vote lists for the proposer schedule — i.e., the exact same class of bug as the analog: an attribute-style getter racing an unlocked mutator of a shared pointer/slice field that a bus/device (`cdx`) — here a kaiax module used by RPC and consensus — depends on for correctness.

### Impact Explanation
Both `getCouncilDB`/`getValidIstanbulSnapshotBefore` (council resolution) and `scanBlocks`/`getRemoveVotesInInterval` (proposer list resolution) feed directly into which validators are treated as council members and who is selected as block proposer. If two honest nodes race this cache differently (e.g., one node's RPC/API-triggered proposer computation runs concurrently with its own block-insertion `applyBlock` call, while another node's does not race), they can compute different councils/proposer lists for the same block height, producing a **state divergence between honest nodes** — the exact impact class called out as acceptable evidence in the validation rubric, and analogous in severity to the original UAF's "read a value racing an update, printed to the security-sensitive interface."

### Likelihood Explanation
Any external caller that triggers proposer/council computation via RPC (a normal, unprivileged reachable path — the same category of "public RPC caller" explicitly allowed) executes concurrently with the node's own block-processing goroutine calling `applyBlock`. This overlap is not attacker-controlled in timing precision, but it is a genuine, always-present data race in a hot path (every block with a validator vote triggers a cache write), making the race condition reachable under normal network operation without requiring a malicious peer, validator, or privileged access — consistent with the required "unprivileged... public-RPC caller" reachability constraint.

### Recommendation
Guard `lowestScannedVoteNumCache` and `validatorVoteBlockNumsCache` (and their invalidation in `applyBlock`) with the same `sync.RWMutex` (or equivalent) used elsewhere in the module, ensuring readers (`readLowestScannedVoteNumCached`, `readValidatorVoteBlockNumsCached`) take a read lock and writers (`applyBlock`'s cache invalidation, and the initial population) take a write lock — mirroring the recommended fix pattern for `driver_override_show()`/`driver_override_store()` (i.e., matching locking on both the read and write sides of the shared field).

### Proof of Concept
1. Run a Kaia CN node with the `valset` kaiax module enabled and governance validator voting active (headers containing `AddValidator`/`RemoveValidator` votes).
2. Concurrently with normal block insertion (which calls `applyBlock` → potentially sets `v.validatorVoteBlockNumsCache = nil`), issue repeated RPC calls that resolve council/proposer at various block numbers (paths through `getCouncilDB` → `readValidatorVoteBlockNumsCached`, and `scanBlocks` → `readValidatorVoteBlockNumsCached`).
3. Run under `go test -race` (as already done for other modules in this repo, e.g. `kaiax/gov/headergov/impl/getter_test.go`'s `TestGetPartialParamSet_ConcurrentAccess`) with a similarly constructed concurrent writer/reader harness against `ValsetModule.validatorVoteBlockNumsCache`/`lowestScannedVoteNumCache`; the race detector will flag the concurrent unlocked read/write, and functionally the reader can be shown returning stale `nums`/`pMinVoteNum` immediately after a concurrent `applyBlock` write, altering the computed council/proposer result for the same block number across repeated calls.

### Citations

**File:** kaiax/valset/impl/getter_council.go (L59-78)
```go
func (v *ValsetModule) getCouncilDB(num uint64) (*valset.AddressSet, bool, error) {
	pMinVoteNum := v.readLowestScannedVoteNumCached()
	if pMinVoteNum == nil {
		return nil, false, errNoLowestScannedNum
	}
	nums := v.readValidatorVoteBlockNumsCached()
	if nums == nil {
		return nil, false, errNoVoteBlockNums
	}

	voteNum := lastNumLessThan(nums, num)
	if voteNum < *pMinVoteNum {
		// found voteNum is not one of the scanned vote nums, i.e. the migration is not yet complete.
		// Return false to indicate that the data is not yet available.
		return nil, false, nil
	} else {
		council := valset.NewAddressSet(ReadCouncil(v.ChainKv, voteNum))
		return council, true, nil
	}
}
```

**File:** kaiax/valset/impl/getter_council.go (L80-98)
```go
func (v *ValsetModule) readLowestScannedVoteNumCached() *uint64 {
	if v.lowestScannedVoteNumCache == nil {
		v.lowestScannedVoteNumCache = ReadLowestScannedVoteNum(v.ChainKv)
	}
	return v.lowestScannedVoteNumCache
}

func (v *ValsetModule) readValidatorVoteBlockNumsCached() []uint64 {
	if v.validatorVoteBlockNumsCache == nil {
		v.validatorVoteBlockNumsCache = ReadValidatorVoteBlockNums(v.ChainKv)
		if v.validatorVoteBlockNumsCache == nil {
			return nil
		}
	}

	nums := make([]uint64, len(v.validatorVoteBlockNumsCache))
	copy(nums, v.validatorVoteBlockNumsCache)
	return nums
}
```

**File:** kaiax/valset/impl/getter_council.go (L189-217)
```go
func (v *ValsetModule) getValidIstanbulSnapshotBefore(snapshotNum uint64) (*valset.AddressSet, error) {
	if snapshotNum == 0 {
		return v.getCouncilGenesis()
	}

	nums := v.readValidatorVoteBlockNumsCached()
	if nums == nil {
		return nil, errNoVoteBlockNums
	}

	// If there were no votes in the range [lowestScannedVoteNum, snapshotNum],
	// we fall back to the nearest snapshot *before* `lowestScannedVoteNum`.
	var header *types.Header
	if pMinVoteNum := v.readLowestScannedVoteNumCached(); pMinVoteNum != nil && *pMinVoteNum < snapshotNum && lastNumLessThan(nums, snapshotNum) < *pMinVoteNum {
		header = v.Chain.GetHeaderByNumber(roundDown(*pMinVoteNum, istanbulCheckpointInterval))
	} else {
		header = v.Chain.GetHeaderByNumber(snapshotNum)
	}

	if header == nil {
		return nil, errNoHeader
	}

	council := valset.NewAddressSet(ReadIstanbulSnapshot(v.ChainKv, header.Hash()))
	if council.Len() == 0 {
		return nil, ErrNoIstanbulSnapshot(snapshotNum)
	}
	return council, nil
}
```

**File:** kaiax/valset/impl/getter_council.go (L219-231)
```go
func (v *ValsetModule) applyBlock(council *valset.AddressSet, num uint64, write bool) error {
	header := v.Chain.GetHeaderByNumber(num)
	if header == nil {
		return errNoHeader
	}
	governingNode := v.GovModule.GetParamSet(num).GoverningNode
	if applyVote(header, council, governingNode) && write {
		insertValidatorVoteBlockNums(v.ChainKv, num)
		writeCouncil(v.ChainKv, num, council.List())
		v.validatorVoteBlockNumsCache = nil
	}
	return nil
}
```

**File:** kaiax/valset/impl/getter_proposers.go (L111-128)
```go
// scanBlocks returns the block numbers to be scanned for remove validator votes
func (v *ValsetModule) scanBlocks(pUpdateNum, pUpdateInterval uint64) []uint64 {
	scanBlocks := make([]uint64, 0)

	// if migrated, scanBlocks is set as voteBlockNums between [pUpdateNum, pUpdateNum+pUpdateInterval)
	pMinVoteNum := v.readLowestScannedVoteNumCached()
	if pMinVoteNum != nil && *pMinVoteNum <= pUpdateNum {
		return slices.DeleteFunc(v.readValidatorVoteBlockNumsCached(), func(n uint64) bool {
			return !(n >= pUpdateNum && n < pUpdateNum+pUpdateInterval)
		})
	}

	// if not migrated, scanBlocks is set as blocknums between [updateNum, pUpdateNum+pUpdateInterval)
	for i := pUpdateNum; i < pUpdateNum+pUpdateInterval; i++ {
		scanBlocks = append(scanBlocks, i)
	}
	return scanBlocks
}
```
