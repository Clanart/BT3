Confirmed: `getOrSetCachedReceipt` returns `(nil, false)` on any lookup error (including `receipts.ErrNotFound`), since `getOrSetCachedReceiptErr` propagates `k.GetReceipt`'s error and returns `nil, err` on failure [1](#0-0) . In `EncodeTmBlock`, the `MsgEVMTransaction` branch correctly checks the returned bool and `continue`s on miss, but the `wasmtypes.MsgExecuteContract` branch discards it (`receipt, _ := getOrSetCachedReceipt(...)`) and then unconditionally dereferences `receipt.From`, `receipt.LogsBloom`, and `receipt.GasUsed` [2](#0-1) .

### Title
Nil pointer dereference in `eth_getBlockBy*` when a Cosmos tx's synthetic EVM receipt is missing - (File: evmrpc/block.go)

### Summary
`EncodeTmBlock`, which backs `eth_getBlockByNumber`/`eth_getBlockByHash`, iterates block messages and for `wasmtypes.MsgExecuteContract` entries calls `getOrSetCachedReceipt` while discarding the `found` boolean, then immediately dereferences the returned `*types.Receipt` pointer. If no synthetic receipt exists for that Cosmos tx hash, the function returns `(nil, false)`, and the subsequent field accesses cause a nil pointer dereference, crashing the RPC goroutine handling the request — analogous to btrfs's `scrub_find_fill_first_stripe` dereferencing a nil `extent_root` when the assumed-always-present structure is actually absent.

### Finding Description
`getOrSetCachedReceiptErr` returns `nil, err` whenever `k.GetReceipt` fails (e.g., `receipts.ErrNotFound`) [3](#0-2) , and `getOrSetCachedReceipt` simply converts that into `(receipt, err == nil)` — so `found == false` implies `receipt == nil` [4](#0-3) .

In `EncodeTmBlock`, the EVM-tx case guards against this (`if !found { continue }`) before touching `receipt.LogsBloom`/`receipt.GasUsed` [5](#0-4) . The wasm-tx case does not: it calls the same helper but drops the boolean (`receipt, _ := getOrSetCachedReceipt(...)`), then unconditionally does `common.HexToAddress(receipt.From)`, builds an `RPCTransaction` from `receipt`, and later dereferences `receipt.LogsBloom` and `receipt.GasUsed` outside the `fullTx` branch as well [6](#0-5) . A synthetic EVM receipt for a Cosmos `MsgExecuteContract` is only created if the wasm execution emitted CW→EVM-relevant events (see `AddCosmosEventsToEVMReceiptIfApplicable`, which returns early via `if len(logs) == 0 { return }` without writing any transient receipt) [7](#0-6) . Consequently, any block containing a `MsgExecuteContract` transaction that doesn't produce CW-side logs relevant to EVM translation (a normal, common, unprivileged wasm execute call) has no receipt at that hash, and `eth_getBlockByNumber`/`eth_getBlockByHash` for that block will nil-deref and crash the serving goroutine.

### Impact Explanation
This crashes the goroutine handling a standard, frequently-used public JSON-RPC call (`eth_getBlockByNumber`/`eth_getBlockByHash`) on any default-configuration full/RPC node whose block contains an ordinary CosmWasm execute transaction. Since block contents (including arbitrary wasm executes) are attacker-controllable via normal transaction submission, and any client can subsequently query that block over JSON-RPC, this is a straightforward crash of default-configuration RPC nodes reachable by an unprivileged actor — satisfying the "crash of default-configuration RPC nodes" impact criterion.

### Likelihood Explanation
High. No special privileges are required: any account can submit a `MsgExecuteContract` to any CosmWasm contract that does not emit CW20/CW721/CW1155-style translatable events (i.e., most ordinary contract calls), and any RPC client (including the attacker) can then call `eth_getBlockByNumber`/`eth_getBlockByHash` for that block with `fullTx=true` (or even `fullTx=false`, since the crash path executes before the `fullTx` branch) to trigger the panic.

### Recommendation
In the `wasmtypes.MsgExecuteContract` branch of `EncodeTmBlock`, check the `found` boolean returned by `getOrSetCachedReceipt` and skip/continue (mirroring the `MsgEVMTransaction` branch) instead of unconditionally dereferencing a potentially nil `receipt`.

### Proof of Concept
1. Submit a Cosmos `MsgExecuteContract` transaction targeting a CosmWasm contract that performs no bank/CW20/CW721/CW1155-translatable action (so `AddCosmosEventsToEVMReceiptIfApplicable` exits early at `len(logs) == 0` and no transient/synthetic EVM receipt is written for that tx hash).
2. Once the block containing this transaction is committed, call `eth_getBlockByNumber` (or `eth_getBlockByHash`) for that block height via the public JSON-RPC endpoint.
3. `EncodeTmBlock` reaches the `*wasmtypes.MsgExecuteContract` case, calls `getOrSetCachedReceipt`, gets `(nil, false)`, discards the `false`, and dereferences `receipt.From`/`receipt.LogsBloom`/`receipt.GasUsed`, panicking the RPC-serving goroutine.

### Citations

**File:** evmrpc/filter.go (L88-108)
```go
func getOrSetCachedReceipt(cacheCreationMutex *sync.Mutex, globalBlockCache BlockCache, ctx sdk.Context, k *keeper.Keeper, block *coretypes.ResultBlock, txHash common.Hash) (*evmtypes.Receipt, bool) {
	receipt, err := getOrSetCachedReceiptErr(cacheCreationMutex, globalBlockCache, ctx, k, block, txHash)
	return receipt, err == nil
}

// getOrSetCachedReceiptErr is like getOrSetCachedReceipt but surfaces the underlying
// keeper error on a cache miss. Callers that need to distinguish "no receipt for this tx"
// from a real store-level failure (e.g. eth_getBlockReceipts, log filtering) should use
// this variant; the boolean-only form is fine when any miss is treated as "skip".
func getOrSetCachedReceiptErr(cacheCreationMutex *sync.Mutex, globalBlockCache BlockCache, ctx sdk.Context, k *keeper.Keeper, block *coretypes.ResultBlock, txHash common.Hash) (*evmtypes.Receipt, error) {
	blockHeight := block.Block.Height
	if receipt, found := getCachedReceipt(globalBlockCache, blockHeight, txHash); found {
		return receipt, nil
	}
	receipt, err := k.GetReceipt(ctx, txHash)
	if err != nil {
		return nil, err
	}
	setCachedReceipt(cacheCreationMutex, globalBlockCache, blockHeight, block, txHash, receipt)
	return receipt, nil
}
```

**File:** evmrpc/block.go (L371-374)
```go
			receipt, found := getOrSetCachedReceipt(cacheCreationMutex, globalBlockCache, latestCtx, k, block, hash)
			if !found {
				continue
			}
```

**File:** evmrpc/block.go (L389-416)
```go
		case *wasmtypes.MsgExecuteContract:
			th := sha256.Sum256(block.Block.Txs[msg.index])
			receipt, _ := getOrSetCachedReceipt(cacheCreationMutex, globalBlockCache, latestCtx, k, block, th)
			if !fullTx {
				transactions = append(transactions, "0x"+hex.EncodeToString(th[:]))
			} else {
				ti := uint64(len(transactions))
				var to common.Address
				ercAddress, _, exists := k.GetAnyPointeeInfo(ctx, m.Contract)
				if exists {
					to = ercAddress
				} else {
					to = k.GetEVMAddressOrDefault(ctx, sdk.MustAccAddressFromBech32(m.Contract))
				}
				transactions = append(transactions, &export.RPCTransaction{
					BlockHash:        &blockhash,
					BlockNumber:      (*hexutil.Big)(number),
					From:             common.HexToAddress(receipt.From),
					To:               &to,
					Input:            m.Msg.Bytes(),
					Hash:             th,
					TransactionIndex: (*hexutil.Uint64)(&ti),
				})
			}
			var bloom ethtypes.Bloom
			bloom.SetBytes(receipt.LogsBloom)
			bitutil.ORBytes(blockBloom, blockBloom, bloom[:])
			blockGasUsed += int64(receipt.GasUsed) //nolint:gosec
```

**File:** app/receipt.go (L104-107)
```go
	}
	if len(logs) == 0 {
		return
	}
```
