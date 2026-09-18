# Non-Deferred Cache Lock Can Remain Held Forever on a Panic Inside `injectValueUnlocked`/`bulkInjectValuesUnlocked` - ([File: sei-db/db_engine/view/read_cache.go])

### Summary
The Alpine/Xen CVE describes a lock (`p2m` lock) that "remains unavailable indefinitely" once certain error conditions are hit, hanging the whole host. The closest reachable analog in sei-chain is the shared read-cache mutex `readCache.lock` in `sei-db/db_engine/view/read_cache.go`, which is acquired with an explicit `c.lock.Lock()` (not `defer`-protected) in the functions that apply DB-read results to the cache. If any statement executed while the lock is held panics before the matching explicit `c.lock.Unlock()` runs, the mutex is left locked forever, and every subsequent state read that goes through this shared cache — used by the OCC parallel-execution / store-layering read path — blocks indefinitely.

### Finding Description
`injectValueUnlocked` and `bulkInjectValuesUnlocked` both follow the same pattern: acquire `c.lock` explicitly, run several state-mutating helper calls (`setTerminalEntryStateWLocked`, `evictWLocked`, `TakeOutOfServiceWLocked`), and only then call `c.lock.Unlock()` as a plain statement, not via `defer`: [1](#0-0) [2](#0-1) 

Because `Unlock()` is not deferred, any panic raised by `setTerminalEntryStateWLocked`, `evictWLocked`, or `TakeOutOfServiceWLocked` while the lock is held will propagate up through the calling goroutine (the read-scheduler / shard resolution path in `sei-db/db_engine/view/shard.go`) without ever releasing `c.lock`. This mirrors the Xen bug class exactly: an error condition (here, a panic mid-critical-section instead of an early-return-without-unlock) leaves a lock permanently unavailable, and every future caller that needs it — every subsequent `Get` on the shard-backed read cache used by state reads across the OCC/store-layering path (`sei-db/db_engine/view/shard.go`, e.g. lines 196-227 which call `s.cache.LookupWLocked`/`s.cache.ResolveUnlocked` guarded by this same cache) — hangs forever waiting on `c.lock.Lock()`.

This cache underlies the DB-backed read path shared by the view/shard layer that the OCC scheduler and general state reads rely on, so a stuck lock here is not confined to a single transaction or shard; it can stall block processing on any node that hits the panic once.

### Impact Explanation
If the lock is left permanently held, every subsequent read through the affected `readCache` — which serves the OCC parallel-execution / store-layering state read path — will deadlock. Because block processing depends on completing all reads for a block, this manifests as a stalled block (delay far beyond 2.5 seconds) and ultimately a validator/node halt, matching the "block delay" / "validator halt" impact classes accepted by this scan.

### Likelihood Explanation
The likelihood hinges entirely on whether `setTerminalEntryStateWLocked`, `evictWLocked`, or `TakeOutOfServiceWLocked` can panic under a reachable error condition (e.g., unexpected internal state, corrupted DB read result, or a bookkeeping invariant violation triggered by a crafted transaction's read/write pattern). I was not able to fully inspect the implementations of `evictWLocked` and `setTerminalEntryStateWLocked` within the available index to confirm a concrete panic-triggering input, so I cannot certify a fully proven end-to-end trigger from a single external transaction — this is the main uncertainty in this finding. The structural bug (non-deferred unlock across a call chain that is not proven panic-free) is confirmed by direct code inspection; the exploitability (finding a message flow that reliably panics inside the critical section) is not.

### Recommendation
Wrap the lock/unlock pairs in `injectValueUnlocked` and `bulkInjectValuesUnlocked` with `defer c.lock.Unlock()` immediately after `c.lock.Lock()`, so the mutex is always released even if a panic occurs inside the guarded block. Additionally, audit `setTerminalEntryStateWLocked`, `evictWLocked`, and `TakeOutOfServiceWLocked` for any panic-capable operations (map/slice indexing, arithmetic, nil dereference) and either eliminate them or ensure they cannot panic on adversarial inputs.

### Proof of Concept
Not fully constructible with the code visible through the index: a concrete PoC requires demonstrating an input that causes `evictWLocked` or `setTerminalEntryStateWLocked` to panic while `c.lock` is held. I could not verify this trigger condition from the available file contents. Given the index size limits noted in these instructions, a full determination would require a Devin session with complete access to `sei-db/db_engine/view/read_cache.go` (the `evictWLocked`, `setTerminalEntryStateWLocked`, and `TakeOutOfServiceWLocked` bodies) and the LRU/eviction bookkeeping it depends on, to confirm whether a reachable panic path exists.

### Citations

**File:** sei-db/db_engine/view/read_cache.go (L399-428)
```go
func (e *cacheEntry) injectValueUnlocked(key []byte, ch chan readResult, result readResult) {
	c := e.cache
	c.lock.Lock()

	// The failure to report to the manager. The read error wins when both happen, since it is the one
	// the waiter is about to be handed.
	failure := result.err

	if e.status == statusScheduled {
		if result.err != nil {
			// Terminal state so readers already waiting on this entry are not stranded. The manager
			// is bricked below, so the entry is never consulted again — the error reaches the waiter
			// over the bound channel, not from the entry.
			e.setTerminalEntryStateWLocked(key, statusFailed, nil)
		} else if result.value == nil {
			e.setTerminalEntryStateWLocked(key, statusDeleted, nil)
			failure = c.evictWLocked(c.hardCap())
		} else {
			e.setTerminalEntryStateWLocked(key, statusAvailable, result.value)
			failure = c.evictWLocked(c.hardCap())
		}
	}

	// Take the cache out of service regardless of the entry's status: the DB read failed, which is
	// fatal whether or not this entry was still the one waiting on it.
	if result.err != nil {
		c.TakeOutOfServiceWLocked(result.err)
	}

	c.lock.Unlock()
```

**File:** sei-db/db_engine/view/read_cache.go (L440-473)
```go
func (c *readCache) bulkInjectValuesUnlocked(reads []pendingRead) {
	c.lock.Lock()
	var failure error
	for i := range reads {
		// Recorded before the status check below: a failed DB read is fatal whether or not this
		// entry was still the one waiting on it.
		if reads[i].result.err != nil && failure == nil {
			failure = reads[i].result.err
		}

		entry := reads[i].entry
		if entry.status != statusScheduled {
			continue
		}
		key := []byte(reads[i].key)
		result := reads[i].result
		if result.err != nil {
			// Terminal state so readers already waiting on this entry are not stranded. The manager
			// is bricked below, so the entry is never consulted again — the error reaches the waiter
			// over the bound channel, not from the entry.
			entry.setTerminalEntryStateWLocked(key, statusFailed, nil)
		} else if result.value == nil {
			entry.setTerminalEntryStateWLocked(key, statusDeleted, nil)
		} else {
			entry.setTerminalEntryStateWLocked(key, statusAvailable, result.value)
		}
	}
	if failure != nil {
		c.TakeOutOfServiceWLocked(failure)
	}
	if err := c.evictWLocked(c.hardCap()); err != nil && failure == nil {
		failure = err
	}
	c.lock.Unlock()
```
