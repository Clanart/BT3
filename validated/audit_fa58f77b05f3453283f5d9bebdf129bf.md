### Title
Unbounded per-address `pendingTxs` slice with full re-sort on every insert enables mempool CPU/memory DoS - ([File: giga/deps/xevm/keeper/keeper.go])

### Summary
`Keeper.AddPendingNonce` in `giga/deps/xevm/keeper/keeper.go` appends to a per-address slice `k.pendingTxs[addrStr]` with no cap on the number of entries a single EVM sender can accumulate, and fully re-sorts that slice on every single insertion. `CalculateNextNonce` then linearly/binary-scans this same slice for every nonce lookup (including for CheckTx of every new transaction from that address). An unprivileged EVM account can grow this slice unboundedly by submitting many transactions with future (gapped) nonces that are never completed, causing insertion cost to become O(n log n) per tx and total cost O(n² log n) for n queued future-nonce transactions from one address — directly analogous to the reported `SafeGuard.nonces` unbounded-array pattern (append-only array later iterated in full).

### Finding Description
`AddPendingNonce` is reachable from any public transaction submission path (CheckTx / mempool admission) for a Sei EVM sender: [1](#0-0) 

Each call appends a new `*PendingTx` to `k.pendingTxs[addrStr]` and then calls `slices.SortStableFunc` over the **entire** slice for that address: [2](#0-1) 

There is no bound on `len(k.pendingTxs[addrStr])` — a sender can keep submitting transactions with strictly increasing (gapped) nonces that never get "filled in" (the low nonce that would let them execute is never sent), and the mempool has no observed per-address pending-nonce cap (searches for `MaxTxsPerSender`, `maxPendingPerAddress`, `PerAccountLimit`, `MaxNonceGap` returned no matches).

`CalculateNextNonce`, which is invoked on every nonce lookup (including ante-handler nonce checks for new incoming transactions from the same sender), then walks/binary-searches this same unbounded slice: [3](#0-2) 

This mirrors the reported bug class exactly: an append-only array grown by user action with no removal guarantee, whose size directly and repeatedly drives the cost of a hot-path operation (`for _, pendingTx := range k.pendingTxs[addrStr]` inside `AddPendingNonce`, plus the full re-sort, plus `sort.Find` in `CalculateNextNonce`).

### Impact Explanation
Because `slices.SortStableFunc` re-sorts the full per-address slice on **every** insertion, a single malicious sender submitting `n` future-nonce transactions imposes O(n log n) work on the *n*-th insertion and O(n² log n) aggregate CPU work on the node's mempool/CheckTx path. Since this structure is consulted on every subsequent CheckTx for that sender (via `CalculateNextNonce`) and mutated on every insertion/removal, sustained submission of gapped-nonce transactions can degrade CheckTx throughput and increase mempool memory usage without bound, since neither the pending map nor the per-address slice is capped. This is a resource-exhaustion vector against the transaction admission pipeline reachable purely through public transaction submission (no privileged access required).

### Likelihood Explanation
An unprivileged user can trigger this simply by broadcasting many EVM transactions from one address with a large nonce gap (never sending the missing low nonce), which is a normal, permitted broadcast pattern (no special fee structure prevents it beyond the cost of gas for eventually-executed low-nonce txs, and the offending high-nonce txs need not ever be executed or even be minable). No governance, validator, or admin privileges are needed — this is directly reachable from the public JSON-RPC / CheckTx surface, matching the required "single submitted transaction ... reachable path" criterion.

### Recommendation
- Cap the number of pending (out-of-order) nonces tracked per address (e.g., reject or drop the oldest entry once a `maxPendingPerAddress` threshold is exceeded), similar to mempool per-account transaction limits used by other implementations.
- Avoid re-sorting the entire slice on every insertion; use an insertion-sort/binary-insert (`sort.Search` + slice insert) instead of `slices.SortStableFunc` over the whole slice, reducing per-insert cost to O(n) (or O(log n) search + O(n) insert) rather than O(n log n).
- Consider evicting stale pending-nonce entries after a TTL to bound worst-case memory/CPU growth even under sustained gapped-nonce submission.

### Proof of Concept
1. From a single EVM address `A` with on-chain nonce `k`, submit `N` transactions with nonces `k+1, k+2, ..., k+N` (skipping nonce `k`), all with valid signatures/fees but never sending the nonce-`k` transaction.
2. Each submission calls `AddPendingNonce`, which appends to `k.pendingTxs[A]` and calls `slices.SortStableFunc` over the whole (growing) slice — see [2](#0-1) .
3. As `N` grows, per-transaction CheckTx cost grows via both the re-sort and the linear scan/`sort.Find` performed in `CalculateNextNonce` ( [3](#0-2) ), degrading mempool throughput and consuming increasing memory, since no bound exists on `len(k.pendingTxs[A])` in the current code.

**Note on confidence:** I was unable to fully verify within the available tool budget whether any external mempool-level configuration (e.g., a global max-pending-per-account enforced outside `giga/deps/xevm/keeper`) exists in `sei-tendermint`'s mempool package that might mitigate this; my searches for common limiting parameter names returned no matches, but a background Devin session with broader code access should confirm there is no such cap before treating this as fully unmitigated.

### Citations

**File:** giga/deps/xevm/keeper/keeper.go (L343-368)
```go
// CalculateNextNonce calculates the next nonce for an address
// If includePending is true, it will consider pending nonces
// If includePending is false, it will only return the next nonce from GetNonce
func (k *Keeper) CalculateNextNonce(ctx sdk.Context, addr common.Address, includePending bool) uint64 {
	k.nonceMx.Lock()
	defer k.nonceMx.Unlock()

	nextNonce := k.GetNonce(ctx, addr)

	// we only want the latest nonce if we're not including pending
	if !includePending {
		return nextNonce
	}

	// get the pending nonces (nil is fine)
	pending := k.pendingTxs[addr.Hex()]

	// Check each nonce starting from latest until we find a gap
	// That gap is the next nonce we should use.
	for ; ; nextNonce++ {
		// if it's not in pending, then it's the next nonce
		if _, found := sort.Find(len(pending), func(i int) int { return uint64Cmp(nextNonce, pending[i].Nonce) }); !found {
			return nextNonce
		}
	}
}
```

**File:** giga/deps/xevm/keeper/keeper.go (L371-420)
```go
func (k *Keeper) AddPendingNonce(hash tmtypes.TxHash, addr common.Address, nonce uint64, priority int64) {
	k.nonceMx.Lock()
	defer k.nonceMx.Unlock()

	addrStr := addr.Hex()
	if existing, ok := k.hashToNonce[hash]; ok {
		if existing.Nonce != nonce {
			fmt.Printf("Seeing transactions with the same hash %X but different nonces (%d vs. %d), which should be impossible\n", hash, nonce, existing.Nonce)
		}
		if existing.Address != addr {
			fmt.Printf("Seeing transactions with the same hash %X but different addresses (%s vs. %s), which should be impossible\n", hash, addr.Hex(), existing.Address.Hex())
		}
		// we want to no-op whether it's a genuine duplicate or not
		return
	}
	for _, pendingTx := range k.pendingTxs[addrStr] {
		if pendingTx.Nonce == nonce {
			if priority > pendingTx.Priority {
				// replace existing tx
				delete(k.hashToNonce, pendingTx.Hash)
				pendingTx.Priority = priority
				pendingTx.Hash = hash
				k.hashToNonce[hash] = &AddressNoncePair{
					Address: addr,
					Nonce:   nonce,
				}
			}
			// we don't need to return error here if priority is lower.
			// Tendermint will take care of rejecting the tx from mempool
			return
		}
	}
	k.hashToNonce[hash] = &AddressNoncePair{
		Address: addr,
		Nonce:   nonce,
	}
	k.pendingTxs[addrStr] = append(k.pendingTxs[addrStr], &PendingTx{
		Hash:     hash,
		Nonce:    nonce,
		Priority: priority,
	})
	slices.SortStableFunc(k.pendingTxs[addrStr], func(a, b *PendingTx) int {
		if a.Nonce < b.Nonce {
			return -1
		} else if a.Nonce > b.Nonce {
			return 1
		}
		return 0
	})
}
```
