### Title
Unbounded Storage Deletion in `DeleteAccount` Outside EVM Gas Metering Enables Block-Processing DoS - (File: `x/evm/keeper/statedb.go`)

### Summary

`Keeper.DeleteAccount` iterates over every committed storage slot of a self-destructed contract and deletes them one-by-one from the Cosmos KVStore at `StateDB.Commit()` time. This work is completely outside EVM gas metering: the EVM charges a fixed 5,000-gas cost for `SELFDESTRUCT` regardless of storage size, while the actual KV deletion loop runs under an infinite gas meter with zero KV gas config. An attacker who accumulates a large number of storage slots across many transactions can trigger `SELFDESTRUCT` in a single cheap transaction, forcing the block-processing node to perform O(N) unmetered KV deletions in one block, potentially exceeding CometBFT consensus timeouts and halting the chain.

---

### Finding Description

**Root cause — `DeleteAccount` iterates all storage slots with no gas bound:** [1](#0-0) 

```go
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
    // ...
    // clear storage
    k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
        k.SetState(ctx, addr, key, nil)   // one KV delete per slot
        return true
    })
    k.accountKeeper.RemoveAccount(ctx, acct)
    // ...
}
```

`ForEachStorage` opens a prefix iterator over the entire storage namespace of the address and walks every entry: [2](#0-1) 

**This is called from `StateDB.Commit()` under a `cacheCtx` that inherits the infinite gas meter:** [3](#0-2) 

The `origCtx` passed to the StateDB was set up by `SetupEthContext`, which installs an infinite gas meter and zeroes out all KV gas costs: [4](#0-3) 

Therefore every `store.Delete(key.Bytes())` call inside `DeleteAccount` consumes **zero** Cosmos SDK gas. The EVM itself only charged the fixed `SELFDESTRUCT` opcode cost (5,000 gas post-EIP-2929) before execution ended.

**The block gas limit check in the ante handler only bounds EVM gas, not commit-time KV work:** [5](#0-4) 

---

### Impact Explanation

An attacker accumulates N storage slots in a contract over many blocks (each SSTORE costs 20,000 EVM gas, so N ≤ ~2,000 per block). When the attacker finally calls `SELFDESTRUCT` (or uses EIP-6780 create-and-destroy in one tx), the EVM charges only ~26,000 gas for that final transaction, but `StateDB.Commit()` must then perform N unmetered KV iterator steps + N KV deletes. For N in the millions (achievable over hundreds of blocks), this single commit can take several seconds of wall-clock time, exceeding CometBFT's block-processing timeout and causing validators to fail to commit the block, halting the chain.

This matches the **Critical** allowed impact: *"Valid unprivileged transaction, RPC submission, or block-processing path can halt the chain … or cause deterministic validator consensus failure."*

---

### Likelihood Explanation

- Any unprivileged user can deploy a contract and call `SSTORE` in a loop across many transactions.
- The attacker pays proportionally for slot creation (20,000 gas/slot) but the deletion is free from their perspective — the O(N) work is borne by every validator node at commit time.
- The amplification factor is: attacker pays ~26,000 gas for the final `SELFDESTRUCT` tx; validators perform N KV deletes. For N = 500,000 slots (accumulated over ~250 blocks at 40M gas/block), the deletion loop is entirely unmetered.
- No privileged role, governance action, or external dependency is required.

---

### Recommendation

1. **Lazy deletion**: Do not delete storage slots synchronously in `DeleteAccount`. Instead, mark the account as "pending storage wipe" in a transient store and clear slots incrementally across subsequent blocks (similar to how Ethereum handles it via state trie pruning).
2. **Gas metering for commit-time work**: Charge EVM gas proportional to the number of storage slots cleared by `SELFDESTRUCT` (analogous to EIP-2929 storage access costs), so the attacker's gas budget bounds the deletion work in any single transaction.
3. **Bounded iteration**: If synchronous deletion is kept, cap the number of slots deleted per `DeleteAccount` call and require multiple transactions to fully clear large contracts.

---

### Proof of Concept

```
1. Deploy contract C with a loop that writes 500,000 storage slots across 250 blocks
   (each block: ~2,000 SSTOREs × 20,000 gas = 40M gas/block).

2. In block 251, send a single tx calling SELFDESTRUCT on C.
   EVM gas charged: ~26,000 (intrinsic + SELFDESTRUCT opcode).
   Ante handler passes: 26,000 << block gas limit.

3. ApplyTransaction calls StateDB.Commit().
   StateDB.Commit() calls keeper.DeleteAccount(cacheCtx, C).
   DeleteAccount calls ForEachStorage → 500,000 iterator steps + 500,000 store.Delete calls.
   All under infinite gas meter / zero KV gas config → zero gas consumed.

4. Block processing wall-clock time spikes by several seconds.
   If it exceeds CometBFT's TimeoutCommit / TimeoutPropose, validators fail to
   reach consensus on the block, halting the chain.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** x/evm/keeper/statedb.go (L57-74)
```go
// ForEachStorage iterate contract storage, callback return false to break early
func (k *Keeper) ForEachStorage(ctx sdk.Context, addr common.Address, cb func(key, value common.Hash) bool) {
	store := ctx.KVStore(k.storeKey)
	prefix := types.AddressStoragePrefix(addr)

	iterator := storetypes.KVStorePrefixIterator(store, prefix)
	defer iterator.Close()

	for ; iterator.Valid(); iterator.Next() {
		key := common.BytesToHash(iterator.Key())
		value := common.BytesToHash(iterator.Value())

		// check if iteration stops
		if !cb(key, value) {
			return
		}
	}
}
```

**File:** x/evm/keeper/statedb.go (L195-223)
```go
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	acct := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	if acct == nil {
		return nil
	}

	// NOTE: only Ethereum accounts (contracts) can be selfdestructed
	_, ok := acct.(ethermint.EthAccountI)
	if !ok {
		return errorsmod.Wrapf(types.ErrInvalidAccount, "type %T, address %s", acct, addr)
	}

	// clear storage
	k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
		k.SetState(ctx, addr, key, nil)
		return true
	})

	// remove auth account
	k.accountKeeper.RemoveAccount(ctx, acct)

	k.debugLog(ctx, "account suicided",
		"ethereum-address", addr,
		"cosmos-address", cosmosAddr,
	)

	return nil
}
```

**File:** x/evm/statedb/statedb.go (L800-825)
```go
		if obj.selfDestructed {
			// Burn any balance that arrived after SelfDestruct was called (e.g., via a
			// value-bearing CALL to the destroyed address within the same transaction).
			// SelfDestruct already burned the balance present at destruction time, but
			// subsequent AddBalance calls write to the bank without a matching burn.
			// DeleteAccount only removes auth metadata and storage; it never touches the
			// bank balance, so we must drain it here before removing the account.
			//
			// Both operations run inside a single CacheContext so that if DeleteAccount
			// fails after SubBalance, the partial burn is rolled back and the bank is
			// left consistent.
			cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
			cacheCtx, writeCache := s.origCtx.CacheContext()
			// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
			// bridge) held by the destroyed address are not drained and may remain as
			// orphaned bank balances.
			if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
				coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
				if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
					return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
				}
			}
			if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
				return errorsmod.Wrap(err, "failed to delete account")
			}
			writeCache()
```

**File:** ante/interfaces/setup.go (L31-40)
```go
// SetupEthContext is adapted from SetUpContextDecorator from cosmos-sdk, it ignores gas consumption
// by setting the gas meter to infinite
func SetupEthContext(ctx sdk.Context) (newCtx sdk.Context, err error) {
	// We need to setup an empty gas config so that the gas is consistent with Ethereum.
	newCtx = ctx.WithGasMeter(storetypes.NewInfiniteGasMeter()).
		WithKVGasConfig(storetypes.GasConfig{}).
		WithTransientKVGasConfig(storetypes.GasConfig{})

	return newCtx, nil
}
```

**File:** ante/eth.go (L155-163)
```go
		gasWanted += gasLimit
		if gasWanted > blockGasLimit {
			return ctx, errorsmod.Wrapf(
				errortypes.ErrOutOfGas,
				"tx gas (%d) exceeds block gas limit (%d)",
				gasWanted,
				blockGasLimit,
			)
		}
```
