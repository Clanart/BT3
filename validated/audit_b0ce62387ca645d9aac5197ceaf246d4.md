### Title
Bloom Filter Computed Before `PostTxProcessing` Hook Runs, Causing Permanent Bloom/Log Divergence — (`x/evm/keeper/state_transition.go`)

### Summary

In `ApplyTransaction`, the per-transaction bloom filter is computed and stored **before** `PostTxProcessing` is called. Because the hook receives a mutable `*ethtypes.Receipt` and can append or remove entries from `receipt.Logs`, the committed bloom reflects only the pre-hook log set while `res.Logs` (and the block-result log events consumed by the JSON-RPC layer) reflects the post-hook log set. The invariant that the block bloom is the union of all committed transaction log addresses/topics is permanently violated for every transaction whose hook modifies `receipt.Logs`.

---

### Finding Description

The execution order in `ApplyTransaction` is:

**Step 1 — Bloom computed from pre-hook logs and stored** (lines 199–211): [1](#0-0) 

```go
logs := types.LogsToEthereum(res.Logs)          // pre-hook snapshot
if len(logs) > 0 {
    bloom := ethtypes.Bloom{}
    for _, log := range logs {
        bloom.Add(log.Address.Bytes())
        for _, topic := range log.Topics { bloom.Add(topic[:]) }
    }
    k.SetTxBloom(tmpCtx, bloom.Big())            // ← stored NOW, before hook
}
```

**Step 2 — Receipt built from the same pre-hook logs** (lines 218–227): [2](#0-1) 

**Step 3 — Hook runs and may mutate `receipt.Logs`** (lines 232–244): [3](#0-2) 

```go
if err = k.PostTxProcessing(tmpCtx, msg, receipt); err != nil {
    ...
} else if commit != nil {
    commit()                                      // ← commits tmpCtx WITH pre-hook bloom
    tmpCtxCommitted = true
    res.Logs = types.NewLogsFromEth(receipt.Logs) // ← updated to post-hook logs
}
```

The `commit()` call at line 241 persists `tmpCtx` — which contains the bloom computed at step 1 — to the underlying KV store. `res.Logs` is then overwritten with `receipt.Logs` as modified by the hook. The bloom is never recomputed after the hook runs.

The `PostTxProcessing` interface explicitly passes a mutable `*ethtypes.Receipt`: [4](#0-3) 

The code comment at line 243 even acknowledges the hook can alter the log:

> `// Since the post-processing can alter the log, we need to update the result`

It updates `res.Logs` but **not** the bloom.

---

### Impact Explanation

The block-level bloom is the OR of all per-tx blooms, collected in `EndBlock`: [5](#0-4) [6](#0-5) 

The JSON-RPC filter path uses this block bloom as a mandatory gate before reading actual logs: [7](#0-6) 

```go
func (f *Filter) blockLogs(blockRes *tmrpctypes.ResultBlockResults, bloom ethtypes.Bloom) ([]*ethtypes.Log, error) {
    if !bloomFilter(bloom, f.criteria.Addresses, f.criteria.Topics) {
        return []*ethtypes.Log{}, nil   // ← entire block skipped on bloom miss
    }
    ...
```

If a hook appends a log with address `A` to `receipt.Logs`, address `A` is absent from the stored bloom. Any `eth_getLogs` / `eth_subscribe logs` query filtering on address `A` will fail the bloom check and return zero results for that block, even though the log is present in the block results. This is a **permanent, non-recoverable false negative** — the bloom is written once and never corrected.

---

### Likelihood Explanation

Hooks that append synthetic logs to `receipt.Logs` are the **primary documented use case** of the hook system (e.g., the Evmos ERC-20 module appending a native `Transfer` log). Any chain that registers such a hook is affected for every transaction that triggers it. The attacker's role is simply to submit a transaction that causes the hook to fire — no privilege is required.

---

### Recommendation

Move the bloom computation to **after** `PostTxProcessing` returns successfully, using the final `receipt.Logs`:

```go
if err = k.PostTxProcessing(tmpCtx, msg, receipt); err != nil {
    res.Logs = nil
} else if commit != nil {
    commit()
    tmpCtxCommitted = true
    res.Logs = types.NewLogsFromEth(receipt.Logs)

    // Recompute bloom from the final (post-hook) log set
    finalLogs := types.LogsToEthereum(res.Logs)
    if len(finalLogs) > 0 {
        bloom := ethtypes.Bloom{}
        for _, log := range finalLogs {
            bloom.Add(log.Address.Bytes())
            for _, topic := range log.Topics { bloom.Add(topic[:]) }
        }
        k.SetTxBloom(tmpCtx, bloom.Big())
    }
}
```

Remove the early bloom computation block at lines 202–211.

---

### Proof of Concept

1. Register a hook that appends one extra log (address `0xDEAD...`) to `receipt.Logs` and returns `nil`.
2. Submit any EVM transaction that succeeds (e.g., a simple ETH transfer).
3. After the block is committed, call `eth_getLogs` with `address: ["0xDEAD..."]` for that block.
4. **Expected (correct):** one log returned.
5. **Actual:** zero logs returned — the block bloom does not contain `0xDEAD...`, so `blockLogs` returns early at the `bloomFilter` check without reading the actual block results.
6. Confirm by directly reading the block result events: the log IS present in the stored events, proving the bloom is the sole source of the miss.

### Citations

**File:** x/evm/keeper/state_transition.go (L199-211)
```go
	logs := types.LogsToEthereum(res.Logs)

	// Compute block bloom filter
	if len(logs) > 0 {
		bloom := ethtypes.Bloom{}
		for _, log := range logs {
			bloom.Add(log.Address.Bytes())
			for _, topic := range log.Topics {
				bloom.Add(topic[:])
			}
		}
		k.SetTxBloom(tmpCtx, bloom.Big())
	}
```

**File:** x/evm/keeper/state_transition.go (L218-227)
```go
	receipt := &ethtypes.Receipt{
		Type:            ethTx.Type(),
		PostState:       nil, // TODO: intermediate state root
		Logs:            logs,
		TxHash:          cfg.TxConfig.TxHash,
		ContractAddress: contractAddr,
		GasUsed:         res.GasUsed,
		BlockHash:       cfg.TxConfig.BlockHash,
		BlockNumber:     cfg.BlockNumber,
	}
```

**File:** x/evm/keeper/state_transition.go (L229-245)
```go
	if !res.Failed() {
		receipt.Status = ethtypes.ReceiptStatusSuccessful
		// Only call hooks if tx executed successfully.
		if err = k.PostTxProcessing(tmpCtx, msg, receipt); err != nil {
			// If hooks return error, revert the whole tx.
			res.VmError = types.ErrPostTxProcessing.Error()
			k.Logger(ctx).Error("tx post processing failed", "error", err)

			// If the tx failed in post processing hooks, we should clear the logs
			res.Logs = nil
		} else if commit != nil {
			// PostTxProcessing is successful, commit the tmpCtx
			commit()
			tmpCtxCommitted = true
			// Since the post-processing can alter the log, we need to update the result
			res.Logs = types.NewLogsFromEth(receipt.Logs)
		}
```

**File:** x/evm/keeper/hooks.go (L37-43)
```go
func (mh MultiEvmHooks) PostTxProcessing(ctx sdk.Context, msg *core.Message, receipt *ethtypes.Receipt) error {
	for i := range mh {
		if err := mh[i].PostTxProcessing(ctx, msg, receipt); err != nil {
			return errorsmod.Wrapf(err, "EVM hook %T failed", mh[i])
		}
	}
	return nil
```

**File:** x/evm/keeper/abci.go (L50-53)
```go
func (k *Keeper) EndBlock(ctx sdk.Context) error {
	k.CollectTxBloom(ctx)
	k.RemoveParamsCache(ctx)
	return nil
```

**File:** x/evm/keeper/bloom.go (L16-27)
```go
func (k Keeper) CollectTxBloom(ctx sdk.Context) {
	store := prefix.NewObjStore(ctx.ObjectStore(k.objectKey), types.KeyPrefixObjectBloom)
	it := store.Iterator(nil, nil)
	defer it.Close()

	bloom := new(big.Int)
	for ; it.Valid(); it.Next() {
		bloom.Or(bloom, it.Value().(*big.Int))
	}

	k.EmitBlockBloomEvent(ctx, bloom.Bytes())
}
```

**File:** rpc/namespaces/ethereum/eth/filters/filters.go (L213-216)
```go
func (f *Filter) blockLogs(blockRes *tmrpctypes.ResultBlockResults, bloom ethtypes.Bloom) ([]*ethtypes.Log, error) {
	if !bloomFilter(bloom, f.criteria.Addresses, f.criteria.Topics) {
		return []*ethtypes.Log{}, nil
	}
```
