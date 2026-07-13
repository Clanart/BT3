### Title
AnteCache LRU Eviction Enables Targeted Replacement-Transaction DoS — (`ante/cache/antecache.go`, `ante/eth.go`, `evmd/ante/handler_options.go`)

---

### Summary

When `AnteCache` is configured with a finite `maxTx`, an unprivileged attacker can flood the cache with their own `(address, nonce)` entries, silently evicting a victim's entry. The next replacement transaction from the victim then fails with `ErrInvalidSequence` because the Cosmos SDK `checkState` sequence was already incremented by the original CheckTx, and the cache shortcut that would have bypassed that check is gone.

---

### Finding Description

**Step 1 — Original tx accepted, checkState sequence incremented.**

When the victim's original tx (nonce M) passes CheckTx, `CheckAndSetEthSenderNonce` finds a cache miss, verifies `txNonce (M) == expectedNonce (M)`, then calls:

```go
acc.SetSequence(expectedNonce + 1)
ak.SetAccount(ctx, acc)          // writes M+1 into checkState
``` [1](#0-0) 

After the full ante chain succeeds, `newEthAnteHandler` commits the staged nonce into the shared cache:

```go
if finalCtx.IsCheckTx() && !finalCtx.IsReCheckTx() {
    for _, entry := range pendingNonces {
        options.AnteCache.Set(entry.Address, entry.Nonce)
    }
}
``` [2](#0-1) 

The cache now holds `(victim_addr, M)` and `checkState` holds sequence M+1.

**Step 2 — Attacker evicts the victim's cache entry.**

`AnteCache.Set` uses FIFO/oldest-first eviction: when `len(cache) >= maxTx`, the front (oldest) element is deleted before the new entry is pushed to the back:

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
``` [3](#0-2) 

An attacker submits `maxTx + 1` valid transactions from distinct addresses. Each successful CheckTx adds one entry. When the `(maxTx+1)`-th entry is inserted, the victim's entry — the oldest — is silently evicted. No error is returned to the caller; the cache simply discards it.

**Step 3 — Victim's replacement tx is rejected.**

The victim submits a replacement tx (same nonce M, higher gas price). `CheckAndSetEthSenderNonce` now finds:

```go
ex := nonceCache.Exists(fromStr, txNonce)   // false — evicted
// cache-shortcut branch NOT taken
if txNonce != expectedNonce {               // M != M+1 (checkState was incremented)
    return nil, errorsmod.Wrapf(
        errortypes.ErrInvalidSequence, ...)
}
``` [4](#0-3) 

`expectedNonce` is M+1 because `checkState` was mutated by the original tx's CheckTx. The replacement tx is rejected with `ErrInvalidSequence`. The attacker can repeat the flood before every replacement attempt, sustaining the DoS indefinitely.

---

### Impact Explanation

The victim cannot replace or cancel any pending transaction for as long as the attacker keeps the cache saturated. In practice this means:

- A victim who submitted a high-value transfer cannot cancel it by sending a zero-value self-transfer at the same nonce with higher gas.
- A victim whose tx is stuck (low gas price during a fee spike) cannot bump the fee.
- The attacker's cost is proportional to `maxTx` valid transactions per block, which is affordable for a motivated party.

This is a **mempool/ante-handler bug that causes valid user transactions to be permanently rejected** while the original (potentially unwanted) transaction remains queued — fitting the High impact category.

---

### Likelihood Explanation

- `maxTx` is set to `mempoolMaxTxs` at app startup. [5](#0-4) 
- A typical value is in the hundreds to low thousands. An attacker with modest funds can submit that many fee-paying transactions from pre-funded addresses.
- The attack is repeatable every time the victim tries to replace their tx.
- No privileged access, governance, or validator collusion is required — only the ability to submit valid Ethereum transactions.

---

### Recommendation

1. **Do not evict arbitrary entries on overflow.** When the cache is full, either reject the new entry (and log a warning) or, better, use a per-address LRU so that only the attacker's own oldest entries are displaced, never another address's entries.
2. **Alternatively, key the eviction on address**: maintain a per-address entry limit rather than a global FIFO queue, so flooding from address A cannot evict entries for address B.
3. **Or remove the sequence-increment side-effect during CheckTx** so that a cache miss on a replacement tx does not produce a false `ErrInvalidSequence` (i.e., read the sequence from committed state, not checkState, for the nonce comparison).

---

### Proof of Concept

```
maxTx = K

1. victim submits tx(nonce=M)  → CheckTx OK
   checkState[victim].seq = M+1
   AnteCache = { (victim, M) }

2. attacker submits tx_1..tx_{K+1} from distinct addresses → each CheckTx OK
   After tx_K:   AnteCache full at K entries, (victim,M) still present
   After tx_{K+1}: AnteCache evicts (victim,M) [oldest], inserts (attacker_{K+1}, n)
   AnteCache = { (attacker_1,n), ..., (attacker_{K+1},n) }   // victim gone

3. victim submits replacement tx(nonce=M, higher gas)
   CheckAndSetEthSenderNonce:
     Exists(victim, M) → false
     expectedNonce = checkState[victim].seq = M+1
     M != M+1  →  ErrInvalidSequence  ← replacement permanently blocked
```

The existing test `TestAnteCache_DropNewEntriesWhenFull` in `ante/cache/antecache_test.go` (lines 74–82) already documents the eviction behaviour and notes that replacement transactions are broken when the cache is full, confirming this is a known-but-unmitigated code path. [6](#0-5)

### Citations

**File:** ante/eth.go (L291-308)
```go
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
```

**File:** ante/eth.go (L322-326)
```go
		if err := acc.SetSequence(expectedNonce + 1); err != nil {
			return nil, errorsmod.Wrapf(err, "failed to set sequence to %d", acc.GetSequence()+1)
		}

		ak.SetAccount(ctx, acc)
```

**File:** evmd/ante/handler_options.go (L168-172)
```go
		if finalCtx.IsCheckTx() && !finalCtx.IsReCheckTx() {
			for _, entry := range pendingNonces {
				options.AnteCache.Set(entry.Address, entry.Nonce)
			}
		}
```

**File:** ante/cache/antecache.go (L48-56)
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

**File:** evmd/app.go (L802-803)
```go
		AnteCache:         cache.NewAnteCache(mempoolMaxTxs),
	})
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
