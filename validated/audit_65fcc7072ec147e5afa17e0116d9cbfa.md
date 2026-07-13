### Title
AnteCache FIFO Eviction Enables Cross-Address Nonce Replacement Denial — (`ante/cache/antecache.go`, `ante/eth.go`, `evmd/ante/handler_options.go`)

---

### Summary

When `AnteCache` is initialized with `maxTx > 0`, an unprivileged attacker can flood the cache with entries from `maxTx` distinct addresses, evicting a victim's `{address, nonce}` entry via FIFO eviction. When the victim subsequently submits a replacement transaction (same nonce, higher fee), `AnteCache.Exists` returns `false`, the cache shortcut is skipped, and the nonce check compares against the already-incremented `checkState` sequence, producing `ErrInvalidSequence`. The victim's replacement transaction is permanently rejected for the duration of the block.

---

### Finding Description

**AnteCache eviction policy** (`ante/cache/antecache.go:48-56`):

```go
if c.maxTx > 0 && len(c.cache) >= c.maxTx {
    if front := c.order.Front(); front != nil {
        oldKey := front.Value.(TxNonce)
        delete(c.cache, oldKey)
        c.order.Remove(front)
    }
}
elem := c.order.PushBack(key)
c.cache[key] = elem
```

The cache uses FIFO eviction: the oldest entry is unconditionally removed when the cache is full. There is no per-address protection or pinning. [1](#0-0) 

**Cache shortcut in `CheckAndSetEthSenderNonce`** (`ante/eth.go:291-309`):

```go
ex := nonceCache.Exists(fromStr, txNonce)
if ctx.IsCheckTx() && !ctx.IsReCheckTx() && ex {
    continue   // bypass nonce check and sequence increment
}
if txNonce != expectedNonce {
    ...
    return nil, errorsmod.Wrapf(errortypes.ErrInvalidSequence, ...)
}
```

The cache shortcut is the **only** mechanism that allows a replacement tx (same nonce) to pass `CheckTx` after the first tx has already incremented the `checkState` sequence. If the cache entry is absent, the code falls through to the strict nonce equality check. [2](#0-1) 

**Cache is committed after full ante chain succeeds** (`evmd/ante/handler_options.go:165-172`):

```go
if finalCtx.IsCheckTx() && !finalCtx.IsReCheckTx() {
    for _, entry := range pendingNonces {
        options.AnteCache.Set(entry.Address, entry.Nonce)
    }
}
``` [3](#0-2) 

**Attack sequence** (concrete, with `maxTx = N`):

1. Victim submits tx with nonce `K` → `CheckTx` passes → `checkState` sequence incremented to `K+1` → `AnteCache.Set(victim, K)` called → cache entry `{victim, K}` at front of FIFO list.
2. Attacker submits `N` txs from `N` distinct funded addresses → each `CheckTx` succeeds → `AnteCache.Set(attackerI, MI)` called `N` times → cache fills to `maxTx`, evicting `{victim, K}` (the oldest entry).
3. Victim submits replacement tx (nonce `K`, higher gas price) → `Exists(victim, K)` = `false` → shortcut skipped → `expectedNonce = K+1` (from `checkState`) → `txNonce = K` → `K != K+1` → `ErrInvalidSequence`.

The test comment in `ante/cache/antecache_test.go:68-73` explicitly documents this failure mode:

> "Right now Set simply returns when len(cache) >= maxTx without signalling failure, so callers assume the nonce was cached. When that happens, a replacement tx never sees its nonce in the cache and gets rejected with ErrInvalidSequence, effectively disabling nonce replacement once a node hits maxTx" [4](#0-3) 

The existing test `TestAnteCache_DropNewEntriesWhenFull` only covers same-address nonce replacement (nonce 1 → nonce 2 for the same address), which works because the new nonce is a different key. It does **not** cover cross-address eviction of a victim's entry. [5](#0-4) 

---

### Impact Explanation

The victim's replacement transaction is rejected with `ErrInvalidSequence` for the entire block duration. The victim cannot bump gas price on a stuck pending transaction. The original (lower-fee) tx remains in the mempool but may not be included by validators preferring higher-fee txs. This is a targeted, repeatable mempool griefing attack that falls under the "mempool handling bug" High impact category.

---

### Likelihood Explanation

The attacker requires `maxTx` funded addresses, each with enough balance to pass `CheckEthCanTransfer`. The cost scales linearly with `maxTx` and the prevailing gas price. For typical mempool sizes (hundreds to low thousands), this is economically feasible for a motivated attacker. The attack window is one block interval, and it can be repeated every block.

---

### Recommendation

Replace FIFO eviction with an address-aware eviction policy: when the cache is full and a new `{address, nonce}` entry must be added, prefer evicting an entry from a **different** address than the one being inserted. Alternatively, use a per-address LRU bucket so that a victim's entry can only be displaced by another entry from the same address (i.e., a newer nonce from the same sender). A simpler fix is to make `Set` a no-op when the cache is full (dropping new entries from other addresses) rather than evicting existing ones — the test comment at line 69-73 already describes this as the intended behavior.

---

### Proof of Concept

```go
// maxTx=1, two addresses: victim and attacker
cache := NewAnteCache(1)

// Step 1: victim's first tx accepted, nonce cached
cache.Set("victim", 5)
// cache = [{victim,5}]

// Step 2: attacker floods with 1 tx from different address
cache.Set("attacker", 0)
// cache full: evicts {victim,5}, adds {attacker,0}
// cache = [{attacker,0}]

// Step 3: victim's replacement tx
exists := cache.Exists("victim", 5) // false — evicted
// CheckAndSetEthSenderNonce: expectedNonce=6 (checkState), txNonce=5 → ErrInvalidSequence
``` [6](#0-5) [7](#0-6)

### Citations

**File:** ante/cache/antecache.go (L37-57)
```go
func (c *AnteCache) Set(address string, nonce uint64) {
	if c.maxTx < 0 {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	key := TxNonce{address, nonce}
	if elem, ok := c.cache[key]; ok {
		c.order.MoveToBack(elem)
		return
	}
	if c.maxTx > 0 && len(c.cache) >= c.maxTx {
		if front := c.order.Front(); front != nil {
			oldKey := front.Value.(TxNonce)
			delete(c.cache, oldKey)
			c.order.Remove(front)
		}
	}
	elem := c.order.PushBack(key)
	c.cache[key] = elem
}
```

**File:** ante/eth.go (L285-309)
```go
		expectedNonce := acc.GetSequence()
		txNonce := tx.Nonce()
		fromStr := from.String()

		// if flag is set, we bypass nonce all check verification
		if !unsafeUnOrderedTx {
			ex := nonceCache.Exists(fromStr, txNonce)
			// to support tx replacement, we check if the transaction nonce exists in the cache and if yes we skip
			// nonce verification, and we don't set the sequence
			// We allow skip verification only during CheckTx to keep sequence safe during the execution.
			if ctx.IsCheckTx() && !ctx.IsReCheckTx() && ex {
				continue
			}

			// nonce verification, the sequence needs to be in order
			if txNonce != expectedNonce {
				// delete in case of recheck tx
				if ex {
					nonceCache.Delete(fromStr, txNonce)
				}
				return nil, errorsmod.Wrapf(
					errortypes.ErrInvalidSequence,
					"invalid nonce; got %d, expected %d", txNonce, expectedNonce,
				)
			}
```

**File:** evmd/ante/handler_options.go (L165-172)
```go
		// Only after the full CheckTx ante stack succeeds do we flush the staged
		// nonces into the shared cache; failures exit earlier and leave the cache
		// untouched.
		if finalCtx.IsCheckTx() && !finalCtx.IsReCheckTx() {
			for _, entry := range pendingNonces {
				options.AnteCache.Set(entry.Address, entry.Nonce)
			}
		}
```

**File:** ante/cache/antecache_test.go (L68-82)
```go
// bounded caches should continue tracking the latest nonce
// even after they reach capacity. Right now Set simply returns when
// len(cache) >= maxTx without signalling failure, so callers assume the nonce
// was cached. When that happens, a replacement tx never sees its nonce in the
// cache and gets rejected with ErrInvalidSequence, effectively disabling nonce
// replacement once a node hits maxTx and amplifying the leak documented above.
func TestAnteCache_DropNewEntriesWhenFull(t *testing.T) {
	antecache := cache.NewAnteCache(1)
	address := "cosmos1huydeevpz37sd9shv2gqf9p8unc0j89x59cn3c"

	antecache.Set(address, 1)
	antecache.Set(address, 2)

	require.True(t, antecache.Exists(address, 2), "cache should keep track of replacement nonce even when full")
}
```
