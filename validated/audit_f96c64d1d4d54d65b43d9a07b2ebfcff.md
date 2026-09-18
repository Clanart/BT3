Found the analog: `IterateAllWeiBalances` in `sei-cosmos/x/bank/keeper/view.go:253-269` hands out a `sdk.Int` (`*ptr` dereferenced) to a caller callback where `ptr` is a pooled `*big.Int`-backed value that is mutated on every loop iteration and returned to `sync.Pool` after the whole iteration via `defer k.intPool.Put(ptr)`.

### Title
Wei-balance iterator callback exposes a live pooled `*big.Int` that is mutated in place and returned to a shared pool while still referenced - ([File: sei-cosmos/x/bank/keeper/view.go])

### Summary
`BaseViewKeeper.IterateAllWeiBalances` (`sei-cosmos/x/bank/keeper/view.go:253-269`) checks out one `*sdk.Int` from the process-wide `SdkIntPool` (`sei-cosmos/x/bank/keeper/pool.go:1-36`) for the whole iteration and unmarshals each store entry's bytes into the same underlying `*big.Int` on every loop step, then passes `*ptr` by value to the caller's callback [1](#0-0) . `sdk.Int.Unmarshal` explicitly reuses the receiver's existing `*big.Int` pointer instead of allocating a new one when non-nil [2](#0-1) , and the pool doc itself states "the sdk.Int passed to cb shares its underlying memory with the pool and is only valid for the duration of that callback invocation" [3](#0-2) . Any caller that retains the `sdk.Int` value (stores it in a slice/map, closes over it, or hands it to a goroutine) beyond the callback invocation ends up holding a value whose backing `*big.Int` is silently mutated by the next iteration and, after the whole call returns, is placed back into the shared `sync.Pool` where any unrelated concurrent balance read elsewhere in the keeper (`GetSupply`, `GetWeiBalance`) can pull the same object and overwrite it while the stale reference is still being read/compared.

### Finding Description
This mirrors the CVE-2016-9137 bug class conceptually: a "wakeup"/reconstruction path hands back an object whose internal pointer is reused/aliased rather than being a fresh, independently-owned value, so a caller that keeps a reference around outlives the object's intended lifetime and observes attacker/state-controlled mutation of memory it believes it owns. Here the "reuse-without-fresh-copy" contract is explicit in code comments (`sei-cosmos/x/bank/keeper/view.go:249-252`, `sei-cosmos/x/bank/keeper/pool.go:9-12`), but nothing in the `sdk.Int` type or the `SdkIntPool` API prevents a caller of `IterateAllWeiBalances` from copying the `sdk.Int` struct (which just copies the `*big.Int` pointer, not the big.Int contents — see `Int{i *big.Int}` in `sei-cosmos/types/int.go:79-81`) into a longer-lived structure. Contrast with `GetWeiBalance` and `GetSupply`, which correctly deep-copy via `ptr.BigInt()` (`Int.BigInt()` calls `new(big.Int).Set(...)`, `sei-cosmos/types/int.go:83-89`) before returning the pooled pointer, at `sei-cosmos/x/bank/keeper/view.go:244` and `sei-cosmos/x/bank/keeper/keeper.go:307-314`. `IterateAllWeiBalances`, by contrast, hands the raw aliased value directly to the callback with no forced copy, relying purely on a doc comment for correctness.

### Impact Explanation
If any consumer of `IterateAllWeiBalances` (used for wei-balance bookkeeping/migration/export paths reachable from RPC-exposed queries or EndBlock accounting logic touching the usei/wei bridge) does not defensively call `.BigInt()`/copy before persisting or comparing the value later, subsequent iterations (same call) or concurrent unrelated balance lookups (different call, since the pool is shared across the `BaseViewKeeper`) can corrupt the value the caller believes is a specific account's wei balance, producing incorrect balance accounting, incorrect supply/balance validation, or masking legitimate balances — a fund-accounting integrity issue on the usei/wei StateDB bridge.

### Likelihood Explanation
The unsafe aliasing is real and demonstrable by direct code reading (`Unmarshal` reuse + shared `sync.Pool` + no deep copy in the iterator callback path), but exploitability strictly depends on whether any call site inside the codebase actually violates the "copy before retaining" contract; I could not find a concrete calling function that retains the value incorrectly within index scope, and I could not fully audit every direct caller of `IterateAllWeiBalances` to confirm one exists that misuses it, or whether OCC parallel execution ever runs multiple `BaseViewKeeper`-driven reads against the same `SdkIntPool` instance concurrently (all pool call sites in this keeper appear to be issued from a single-threaded read path per call). This limits confidence that the aliasing is remotely triggerable for concrete fund loss versus being a purely internal, currently-safe optimization whose contract is only enforced by comments.

### Recommendation
Not applicable in ask-only mode — no plan is being generated. Conceptually, `IterateAllWeiBalances` should deep-copy (`sdk.NewIntFromBigInt(ptr.BigInt())`) before invoking the callback, matching `GetWeiBalance`/`GetSupply`, or the `SdkIntPool` should be scoped per-call instead of shared across the keeper so aliasing cannot cross unrelated queries.

### Proof of Concept
Not able to construct a concrete transaction/RPC PoC from the indexed code alone: exploitation requires identifying a caller of `IterateAllWeiBalances` that retains the passed `sdk.Int` past the callback (e.g., appends it to a slice) and a concurrent or subsequent balance read that reuses the same pooled pointer before that retained value is consumed/copied. I was unable to locate such a caller within the indexed portion of the codebase, so this should be treated as a latent aliasing hazard rather than a confirmed exploit path — a full-repository review (via a Devin session with complete file access) would be needed to check every consumer of `IterateAllWeiBalances` for this misuse pattern.

### Citations

**File:** sei-cosmos/x/bank/keeper/view.go (L249-252)
```go
// IterateAllWeiBalances iterates over all wei balances. The sdk.Int passed to
// cb shares its underlying memory with the pool and is only valid for the
// duration of that callback invocation. Callers that need to retain the value
// past the callback must copy it via i.BigInt().
```

**File:** sei-cosmos/x/bank/keeper/view.go (L253-269)
```go
func (k BaseViewKeeper) IterateAllWeiBalances(ctx sdk.Context, cb func(sdk.AccAddress, sdk.Int) bool) {
	store := prefix.NewStore(ctx.KVStore(k.storeKey), types.WeiBalancesPrefix)

	iterator := store.Iterator(nil, nil)
	defer func() { _ = iterator.Close() }()

	ptr := k.intPool.Get()
	defer k.intPool.Put(ptr)

	for ; iterator.Valid(); iterator.Next() {
		if err := ptr.Unmarshal(iterator.Value()); err != nil {
			panic(err)
		}
		if cb(iterator.Key(), *ptr) {
			break
		}
	}
```

**File:** sei-cosmos/types/int.go (L417-442)
```go
// Unmarshal implements the gogo proto custom type interface.
func (i *Int) Unmarshal(data []byte) error {
	// maxBitLen is 256, which requires ~78 decimal digits, so 100 is a safe upper
	// bound (with room for sign).
	if len(data) > 100 {
		return fmt.Errorf("integer string too long: got %d, max 100", len(data))
	}
	if len(data) == 0 {
		*i = ZeroInt()
		return nil
	}

	if i.i == nil {
		i.i = new(big.Int)
	}

	if err := i.i.UnmarshalText(data); err != nil {
		return err
	}

	if i.i.BitLen() > maxBitLen {
		return fmt.Errorf("integer out of range; got: %d, max: %d", i.i.BitLen(), maxBitLen)
	}

	return nil
}
```
