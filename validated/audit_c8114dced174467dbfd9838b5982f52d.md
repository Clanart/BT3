Let me trace through the relevant code to verify this claim.

## Vulnerability Analysis

The claim is concrete and traceable through the production code. Here is the full trace:

### Step 1 — Bloom is computed from pre-hook logs

In `ApplyTransaction`, the bloom is computed from `res.Logs` (the raw EVM execution result) and stored via `SetTxBloom` **before** `PostTxProcessing` is called: [1](#0-0) 

```
logs := types.LogsToEthereum(res.Logs)   // EVM-only logs

if len(logs) > 0 {
    bloom := ethtypes.Bloom{}
    for _, log := range logs { bloom.Add(...) }
    k.SetTxBloom(tmpCtx, bloom.Big())     // ← bloom stored HERE
}
```

### Step 2 — PostTxProcessing can append new logs to receipt.Logs

`PostTxProcessing` receives `receipt` (whose `Logs` field is the same `logs` slice) and hooks are explicitly permitted to mutate it. The code even has a comment acknowledging this: [2](#0-1) 

```
// Since the post-processing can alter the log, we need to update the result
res.Logs = types.NewLogsFromEth(receipt.Logs)
```

The hook interface signature confirms hooks receive a mutable `*ethtypes.Receipt`: [3](#0-2) 

### Step 3 — CollectTxBloom aggregates only the pre-hook blooms

`EndBlock` calls `CollectTxBloom`, which ORs together all per-tx blooms stored by `SetTxBloom` and emits the `EventBlockBloom` event: [4](#0-3) [5](#0-4) 

Any address/topic added to `receipt.Logs` by a hook is **never** included in this block bloom.

### Step 4 — blockLogs uses the block bloom as a hard gate

In the range-query path of `eth_getLogs`, `blockLogs` calls `bloomFilter` first. If the bloom does not contain the queried address/topic, the block is **skipped entirely** — `GetLogsFromBlockResults` is never called: [6](#0-5) 

```go
func (f *Filter) blockLogs(...) ([]*ethtypes.Log, error) {
    if !bloomFilter(bloom, f.criteria.Addresses, f.criteria.Topics) {
        return []*ethtypes.Log{}, nil   // ← block silently skipped
    }
```

The bloom used here comes from `BlockBloom()`, which reads the `EventBlockBloom` event emitted by `CollectTxBloom`: [7](#0-6) [8](#0-7) 

### Step 5 — The actual committed logs DO include the hook-added logs

`GetLogsFromBlockResults` reads from `blockRes.TxsResults`, which contains `res.Logs` after the post-hook update at line 244. So the logs are committed correctly — only the bloom is wrong: [9](#0-8) 

---

### Verdict

The divergence is real and concrete:

| What | Source |
|---|---|
| Bloom computed from | `res.Logs` (pre-`PostTxProcessing`) |
| Logs stored in block results | `receipt.Logs` (post-`PostTxProcessing`) |
| `blockLogs` pre-filter uses | block bloom (missing hook-added entries) |

**The invariant "block bloom ⊇ all committed log addresses/topics" is broken whenever a `PostTxProcessing` hook appends a log whose address or topic was not already present in the EVM execution logs.**

An unprivileged tx submitter can trigger this by submitting any tx that causes a registered hook to append such a log (e.g., an ERC20 transfer that causes the ERC20 hook to append a synthetic log with a different contract address). The result is that `eth_getLogs` range queries silently return no results for that address/topic, even though the log is durably committed in the block.

---

### Title
Block bloom excludes logs added by `PostTxProcessing` hooks, causing `eth_getLogs` to silently omit committed logs — (`x/evm/keeper/state_transition.go`)

### Summary
`SetTxBloom` is called with the pre-hook bloom before `PostTxProcessing` runs. Hooks can append new logs to `receipt.Logs`, but those logs' addresses/topics are never added to the stored bloom. `blockLogs` uses the block bloom as a hard gate; blocks are silently skipped for queries matching only hook-added log addresses/topics.

### Finding Description
In `ApplyTransaction` (`x/evm/keeper/state_transition.go`), the per-tx bloom is computed from `res.Logs` (lines 199–211) and stored via `SetTxBloom` before `PostTxProcessing` is invoked (line 232). `PostTxProcessing` receives a mutable `*ethtypes.Receipt` and may append new logs. The updated `receipt.Logs` is correctly propagated to `res.Logs` (line 244) and stored in block results, but the bloom stored by `SetTxBloom` is never updated. `CollectTxBloom` in `EndBlock` aggregates only the stale per-tx blooms. The `blockLogs` function in the filter path uses this incomplete bloom as a hard gate, causing entire blocks to be skipped for `eth_getLogs` queries that match only hook-added log addresses/topics.

### Impact Explanation
`eth_getLogs` (and `eth_getFilterLogs`) range queries silently return empty results for any address or topic that was introduced exclusively by a `PostTxProcessing` hook. The logs are durably committed and visible via `eth_getTransactionReceipt`, but invisible to log range queries. This is a High-severity JSON-RPC correctness bug: applications relying on `eth_getLogs` for event-driven logic (e.g., bridge relayers, indexers, ERC20 event listeners) will silently miss committed events.

### Likelihood Explanation
Any Ethermint-based chain that registers a `PostTxProcessing` hook that appends logs with new addresses/topics (the documented and intended use case, e.g., the ERC20 module on Evmos) is affected. An unprivileged user submitting a normal tx that triggers the hook is sufficient to reproduce the issue.

### Recommendation
Move the bloom computation to **after** `PostTxProcessing` returns successfully, using the final `receipt.Logs`:

```go
// After PostTxProcessing succeeds:
if commit != nil {
    commit()
    res.Logs = types.NewLogsFromEth(receipt.Logs)
}
// Compute bloom from final receipt.Logs, not pre-hook res.Logs
finalLogs := types.LogsToEthereum(res.Logs)
if len(finalLogs) > 0 {
    bloom := ethtypes.Bloom{}
    for _, log := range finalLogs { ... }
    k.SetTxBloom(tmpCtx, bloom.Big())
}
```

### Proof of Concept
1. Register a `PostTxProcessing` hook that appends a log with address `0xHOOK` and topic `0xHOOKTOPIC` to `receipt.Logs`.
2. Submit any tx that succeeds and triggers the hook.
3. Call `eth_getLogs` with `address: 0xHOOK` over the block range containing the tx.
4. Observe: empty result, despite the log being present in `eth_getTransactionReceipt`.
5. Confirm: `BlockBloom()` for that block does not contain `0xHOOK`.

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

**File:** x/evm/keeper/abci.go (L50-53)
```go
func (k *Keeper) EndBlock(ctx sdk.Context) error {
	k.CollectTxBloom(ctx)
	k.RemoveParamsCache(ctx)
	return nil
```

**File:** rpc/namespaces/ethereum/eth/filters/filters.go (L186-200)
```go
	for height := from; height <= to; height++ {
		blockRes, err := f.backend.TendermintBlockResultByNumber(&height)
		if err != nil {
			f.logger.Debug("failed to fetch block result from Tendermint", "height", height, "error", err.Error())
			return logs, errors.Wrapf(err, "failed to fetch block result for height %d", height)
		}

		bloom, err := f.backend.BlockBloom(blockRes)
		if err != nil {
			return logs, err
		}

		filtered, err := f.blockLogs(blockRes, bloom)
		if err != nil {
			return logs, errors.Wrapf(err, "failed to fetch block by number %d", height)
```

**File:** rpc/namespaces/ethereum/eth/filters/filters.go (L213-216)
```go
func (f *Filter) blockLogs(blockRes *tmrpctypes.ResultBlockResults, bloom ethtypes.Bloom) ([]*ethtypes.Log, error) {
	if !bloomFilter(bloom, f.criteria.Addresses, f.criteria.Topics) {
		return []*ethtypes.Log{}, nil
	}
```

**File:** rpc/backend/blocks.go (L554-568)
```go
// BlockBloom query block bloom filter from block results
func (b *Backend) BlockBloom(blockRes *tmrpctypes.ResultBlockResults) (ethtypes.Bloom, error) {
	for _, event := range blockRes.FinalizeBlockEvents {
		if event.Type != evmtypes.EventTypeBlockBloom {
			continue
		}

		for _, attr := range event.Attributes {
			if bytes.Equal([]byte(attr.Key), bAttributeKeyEthereumBloom) {
				return ethtypes.BytesToBloom([]byte(attr.Value)), nil
			}
		}
	}
	return ethtypes.Bloom{}, errors.New("block bloom event is not found")
}
```

**File:** rpc/backend/utils.go (L313-328)
```go
// GetLogsFromBlockResults returns the list of event logs from the tendermint block result response
func GetLogsFromBlockResults(blockRes *tmrpctypes.ResultBlockResults) ([][]*ethtypes.Log, error) {
	height, err := ethermint.SafeUint64(blockRes.Height)
	if err != nil {
		return nil, err
	}
	blockLogs := [][]*ethtypes.Log{}
	for _, txResult := range blockRes.TxsResults {
		logs, err := evmtypes.DecodeTxLogsFromEvents(txResult.Data, txResult.Events, height)
		if err != nil {
			return nil, err
		}
		blockLogs = append(blockLogs, logs)
	}
	return blockLogs, nil
}
```
