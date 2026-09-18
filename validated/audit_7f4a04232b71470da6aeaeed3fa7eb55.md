### Title
Nil pointer dereference (RPC node DoS) in `GetEvmTxIndex` when an EVM `AssociateTx` message is present in a queried block - ([File: evmrpc/tx.go])

### Summary
`GetEvmTxIndex` in `evmrpc/tx.go` calls `m.AsTransaction()` on every `*types.MsgEVMTransaction` in a block and immediately dereferences the result with `etx.Hash()`, without checking for `nil` and without excluding `AssociateTx` messages first, unlike every other call site in the same package.

### Finding Description
`MsgEVMTransaction.AsTransaction()` is documented to return `(nil, nil)` when the underlying tx data cannot be represented as a standard Ethereum transaction (it calls `UnpackTxData` and builds an `ethtypes.Transaction` from it): [1](#0-0) 

Every other production call site that consumes `AsTransaction()` guards against `IsAssociateTx()` or an explicit `nil` check before dereferencing the result:
- `filterTransactions` skips `IsAssociateTx()` messages before calling `AsTransaction()`: [2](#0-1) 
- `getEthTxForTxBz` explicitly excludes `evmTx.IsAssociateTx()`: [3](#0-2) 
- `BlockByNumber` explicitly checks `ethtx == nil` after `AsTransaction()`: [4](#0-3) 

However, `GetEvmTxIndex` — used to compute a transaction's position/log offset for receipt-related RPC calls — has neither guard: [5](#0-4) 

If a `MsgEVMTransaction` wrapping an `AssociateTx` (or any tx-data variant not convertible into a standard `ethtypes.Transaction`) is included in a block's `indexedMsg` list and reaches `GetEvmTxIndex`, `m.AsTransaction()` returns `(nil, nil)`, and the immediately following `etx.Hash()` call dereferences the nil pointer, panicking the goroutine handling the RPC request.

### Impact Explanation
A panic inside a JSON-RPC handler goroutine that is not recovered crashes/halts request processing for the public EVM JSON-RPC surface of a default-configuration node — matching the accepted impact "crash of default-configuration RPC nodes." Any unprivileged client requesting receipt/log data for a block containing an EVM address-association transaction (a transaction type that any account can submit) could trigger it.

### Likelihood Explanation
`AssociateTx` messages are a normal, unprivileged, user-reachable transaction type used for Sei<->EVM address association, so blocks containing them are common on-chain. Whether `filterTransactions`'s upstream filtering (which does explicitly skip `IsAssociateTx()`) fully prevents such messages from ever reaching `GetEvmTxIndex`'s `msgs` argument could not be fully confirmed within the scope of this review — `getFilteredMsgs`/`filterTransactions` appear to already exclude `IsAssociateTx()` entries before building the `indexedMsg` slice consumed by `GetEvmTxIndex`, based on the code inspected. If that exclusion indeed covers every caller of `GetEvmTxIndex`, the missing nil-check would be dead code (defense-in-depth) rather than an exploitable path, and likelihood would be low. I was not able to trace every caller of `GetEvmTxIndex` end-to-end (e.g., whether `includeSyntheticTxs=true` paths, cache-populated `globalBlockCache` entries, or other message-construction paths could bypass the `IsAssociateTx()` filter) before running out of tool budget, so this should be treated as **unverified** and requires confirmation with a live trace/test before treating it as definitively reachable.

### Recommendation
Add the same `IsAssociateTx()` / `nil`-check guard used elsewhere in `evmrpc/tx.go` and `evmrpc/utils.go` to `GetEvmTxIndex`:
```go
case *types.MsgEVMTransaction:
    if m.IsAssociateTx() {
        continue
    }
    etx, _ = m.AsTransaction()
    if etx == nil {
        continue
    }
    txHash = etx.Hash()
```

### Proof of Concept
Not independently verified end-to-end due to tool-call exhaustion; the concrete PoC would be: submit an `AssociateTx`-backed `MsgEVMTransaction`, have it committed in a block, then call an RPC endpoint that routes through `GetEvmTxIndex` with that block/tx index and observe whether the request panics the RPC handler. This needs confirmation with the actual upstream caller chain of `GetEvmTxIndex` to determine if `AssociateTx` messages can actually reach it (my inspection suggests `filterTransactions` filters them out earlier, which would make this currently unreachable dead code rather than an active vulnerability).

### Citations

**File:** x/evm/types/message_evm_transaction.go (L67-74)
```go
func (msg *MsgEVMTransaction) AsTransaction() (*ethtypes.Transaction, ethtx.TxData) {
	txData, err := UnpackTxData(msg.Data)
	if err != nil {
		return nil, nil
	}

	return ethtypes.NewTx(txData.AsEthereumData()), txData
}
```

**File:** evmrpc/utils.go (L239-244)
```go
			case *types.MsgEVMTransaction:
				if m.IsAssociateTx() {
					continue
				}
				ethtx, _ := m.AsTransaction()
				hash := ethtx.Hash()
```

**File:** evmrpc/tx.go (L416-421)
```go
	evmTx, ok := decoded.GetMsgs()[0].(*types.MsgEVMTransaction)
	if !ok || evmTx.IsAssociateTx() {
		return nil
	}
	ethtx, _ := evmTx.AsTransaction()
	return ethtx
```

**File:** evmrpc/tx.go (L430-441)
```go
func GetEvmTxIndex(ctx sdk.Context, block *coretypes.ResultBlock, msgs []indexedMsg, txIndex uint32, k *keeper.Keeper, cacheCreationMutex *sync.Mutex, globalBlockCache BlockCache) (index int, found bool, etx *ethtypes.Transaction, logIndexOffset int) {
	var evmTxIndex, logIndex int
	for _, msg := range msgs {
		var txHash common.Hash
		switch m := msg.msg.(type) {
		case *types.MsgEVMTransaction:
			etx, _ = m.AsTransaction()
			txHash = etx.Hash()
		case *wasmtypes.MsgExecuteContract:
			etx = nil
			txHash = common.Hash(sha256.Sum256(block.Block.Txs[msg.index]))
		}
```

**File:** evmrpc/simulate.go (L472-480)
```go
			case *types.MsgEVMTransaction:
				if m.IsAssociateTx() {
					continue
				}
				ethtx, _ := m.AsTransaction()
				if ethtx == nil {
					// AsTransaction may return nil if it fails to unpack the tx data.
					continue
				}
```
