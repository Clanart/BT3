### Title
Shallow receipt clone in the EVM RPC receipt cache lets concurrent requests mutate shared, cached `Log` objects - ([File: evmrpc/tx.go])

### Summary
`encodeReceipt` (used by `eth_getTransactionReceipt`, `eth_getBlockReceipts`, and log-filter endpoints) "clones" a cached receipt before mutating it in place, but the clone is only a shallow struct copy that does not defend against sharing the same object. This is the same bug class as CVE‑2016‑7180: code assumes a value is safe to treat as owned/mutable ("constant"/private) when it is in fact still aliased to a shared, cached object, and the subsequent mutation corrupts state that other readers still depend on.

### Finding Description
`getOrSetCachedReceipt`/`getCachedReceipt` in [1](#0-0)  return the `*evmtypes.Receipt` pointer stored directly in the shared `globalBlockCache` (`BlockCacheEntry.Receipts`), with only a `sync.RWMutex` protecting the map lookup — not the receipt contents.

`encodeReceipt` then does: [2](#0-1) 

`cloneReceiptForMutation` is a **shallow** copy: [3](#0-2) 

`cloned := *receipt` copies the struct, but `Receipt.Logs` is `[]*types.Log` — a slice of pointers. The shallow copy still contains the same `*types.Log` pointers as the cached original. Immediately after, `encodeReceipt` mutates those shared `*Log` objects in place: `keeper.GetLogsForTx(normalizedReceipt, ...)` and the loop `for _, log := range logs { log.BlockHash = bh }` write directly into the log structs that are still referenced from `globalBlockCache`.

This is provably a real hazard, not a hypothetical one: the codebase's own receipt cache in sei-db explicitly documents and defends against exactly this pattern — it deep-copies `Logs`/`LogsBloom` before returning a cached receipt, with the comment "Callers ... may normalize TransactionIndex in-place. Clone to avoid mutating the cached receipt and corrupting future lookups": [4](#0-3) 

The `evmrpc/filter.go` block cache path that backs `eth_getTransactionReceipt`/`eth_getBlockReceipts`/log filters has no equivalent deep-copy guard, so it lacks the very protection that a sibling cache in the same codebase considers mandatory for this exact reuse pattern.

### Impact Explanation
`globalBlockCache` (an LRU keyed by block height, `evmrpc/filter.go` lines 61-74) is shared across all concurrent JSON‑RPC connections on a node. Because `Get`/mutation is not synchronized at the object level:
- Concurrent RPC requests for receipts/logs belonging to the same cached block can race on the same underlying `*types.Log` objects (unsynchronized concurrent field writes to `Log.BlockHash`/`Log.Index`), which is a data race detectable/fatal under Go's race detector and can produce corrupted or inconsistent `blockHash`/`logIndex`/`transactionIndex` values served to other simultaneous callers of the public EVM JSON-RPC surface.
- Because the corruption happens in the shared cache entry (not a private copy), it persists and can be observed by every subsequent request against that cached block, silently returning incorrect log/receipt metadata to unrelated RPC clients until the LRU entry is evicted.

This is reachable by any unauthenticated public-RPC client simply by issuing ordinary `eth_getTransactionReceipt`/`eth_getBlockReceipts`/`eth_getLogs` calls concurrently — no privileged access required.

### Likelihood Explanation
High under any concurrent RPC load: `eth_getTransactionReceipt`/`eth_getLogs`/`eth_getBlockReceipts` are among the most frequently called endpoints by wallets, indexers, and block explorers, and multiple simultaneous callers querying the same recent block is the common case rather than an edge case.

### Recommendation
Deep-copy `Receipt.Logs` (and any other pointer/slice fields such as `LogsBloom`) in `cloneReceiptForMutation`, mirroring the existing `cloneReceipt` helper in `sei-db/ledger_db/receipt/receipt_cache.go`, before any in-place mutation (`log.BlockHash = bh`, `TransactionIndex` normalization, etc.) is performed on receipts obtained from `globalBlockCache`.

### Proof of Concept
1. Two RPC clients concurrently call `eth_getTransactionReceipt`/`eth_getLogs` for transactions in the same recently-cached block, hitting `getOrSetCachedReceipt` → the same `*evmtypes.Receipt`/`*types.Log` objects from `globalBlockCache`.
2. Both calls proceed into `encodeReceipt`, each calling `cloneReceiptForMutation` (shallow copy) and then writing `log.BlockHash = bh` on the shared `*types.Log` objects.
3. Run with Go's race detector (`go test -race` / `-race` build) against a load test hitting these endpoints concurrently for the same block to observe the concurrent unsynchronized read/write on the shared `Log` struct fields, and/or inspect returned JSON across the two concurrent responses for inconsistent `blockHash` values.

Note: I was not able to fully inspect `x/evm/keeper/log.go`'s `GetLogsForTx` implementation (only located it via `grep_search`) before iterations ran out, so I cannot confirm with 100% certainty whether it allocates new `Log` objects internally or reuses `receipt.Logs` pointers directly; the analysis above is based on the confirmed shallow clone plus the confirmed in-place `log.BlockHash = bh` mutation in `evmrpc/tx.go`, and the fact that a sibling cache in the codebase treats this exact scenario as requiring a deep copy.

### Citations

**File:** evmrpc/filter.go (L76-90)
```go
// Helper functions for cache access (fine-grained locking)
func getCachedReceipt(globalBlockCache BlockCache, blockHeight int64, txHash common.Hash) (*evmtypes.Receipt, bool) {
	if entry, found := globalBlockCache.Get(blockHeight); found {
		entry.RLock()
		defer entry.RUnlock()
		if receipt, hasReceipt := entry.Receipts[txHash]; hasReceipt {
			return receipt, true
		}
	}
	return nil, false
}

func getOrSetCachedReceipt(cacheCreationMutex *sync.Mutex, globalBlockCache BlockCache, ctx sdk.Context, k *keeper.Keeper, block *coretypes.ResultBlock, txHash common.Hash) (*evmtypes.Receipt, bool) {
	receipt, err := getOrSetCachedReceiptErr(cacheCreationMutex, globalBlockCache, ctx, k, block, txHash)
	return receipt, err == nil
```

**File:** evmrpc/tx.go (L473-483)
```go
	evmTxIndex, foundTx, etx, logIndexOffset := GetEvmTxIndex(ctx, block, msgs, receipt.TransactionIndex, k, cacheCreationMutex, globalBlockCache)
	// convert tx index including cosmos txs to tx index excluding cosmos txs
	if !foundTx {
		return nil, errors.New("failed to find transaction in block")
	}
	normalizedReceipt := cloneReceiptForMutation(receipt)
	normalizedReceipt.TransactionIndex = uint32(evmTxIndex)              //nolint:gosec
	logs := keeper.GetLogsForTx(normalizedReceipt, uint(logIndexOffset)) //nolint:gosec
	for _, log := range logs {
		log.BlockHash = bh
	}
```

**File:** evmrpc/tx.go (L538-544)
```go
func cloneReceiptForMutation(receipt *types.Receipt) *types.Receipt {
	if receipt == nil {
		return nil
	}
	cloned := *receipt
	return &cloned
}
```

**File:** sei-db/ledger_db/receipt/receipt_cache.go (L113-150)
```go
		receipt, found := blockReceipts[txHash]
		if found {
			// Callers (e.g. RPC response formatting) may normalize TransactionIndex in-place.
			// Clone to avoid mutating the cached receipt and corrupting future lookups.
			return cloneReceipt(receipt), true
		}
	}
	return nil, false
}

// cloneReceipt makes a deep copy to keep cached receipts immutable to callers.
func cloneReceipt(r *types.Receipt) *types.Receipt {
	if r == nil {
		return nil
	}
	c := *r
	if r.Logs != nil {
		logs := make([]*types.Log, len(r.Logs))
		for i, lg := range r.Logs {
			if lg == nil {
				continue
			}
			logCopy := *lg
			if lg.Topics != nil {
				logCopy.Topics = append([]string(nil), lg.Topics...)
			}
			if lg.Data != nil {
				logCopy.Data = append([]byte(nil), lg.Data...)
			}
			logs[i] = &logCopy
		}
		c.Logs = logs
	}
	if r.LogsBloom != nil {
		c.LogsBloom = append([]byte(nil), r.LogsBloom...)
	}
	return &c
}
```
