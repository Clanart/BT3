### Title
Unbounded index access into transaction receipts causes a public-RPC-triggerable panic - (File: api/api_eth.go)

### Summary
`EthAPI.GetTransactionReceipt` looks up a transaction's block-level receipts and then indexes into that receipts slice using the transaction's position (`index`) without validating that the slice is non-nil and long enough to contain that index. Any public JSON-RPC caller invoking `eth_getTransactionReceipt` on a hash that hits this code path can crash the node process with an unrecovered panic, mirroring the CVE-2017-15286 bug class where a caller failed to verify that a lookup actually produced a populated result before using the corresponding data structure.

### Finding Description
`GetTransactionReceipt` first resolves the transaction, block hash, block number, and transaction index via `txpoolAPI.GetTxLookupInfoAndReceipt(ctx, hash)`, and only checks that `tx != nil`: [1](#0-0) 

It then retrieves the block's receipts with `txpoolAPI.GetBlockReceipts(ctx, blockHash)` and immediately loops from `0` to `index` (inclusive), summing `receipts[i].GasUsed`, without ever checking that `receipts` is non-nil or that `len(receipts) > index`:
```go
receipts := txpoolAPI.GetBlockReceipts(ctx, blockHash)
cumulativeGasUsed := uint64(0)
for i := uint64(0); i <= index; i++ {
    cumulativeGasUsed += receipts[i].GasUsed
}
```

This is structurally the same root cause as the SQLite `tableColumnList` bug: a step/lookup (`sqlite3_step` there, `GetBlockReceipts` here) is not guaranteed to succeed or to return a fully-populated result for every code path (e.g., missing/pruned receipts for the block, receipts not yet persisted for a given hash, or any db/cache miss returning `nil`/a short slice), yet the caller unconditionally dereferences the associated structure (`receipts[i]`) as though the lookup always yields a validly sized object. If `receipts` is `nil` or shorter than `index+1`, this results in an index-out-of-range panic (the Go analog of a NULL pointer dereference), which is unrecovered and crashes the `geth`/`kaia` node process.

By contrast, the equivalent Kaia-native RPC path, `KaiaTransactionAPI.getTransactionReceipt`, does not perform this unguarded indexing and instead only checks `tx == nil || receipt == nil` in `RpcOutputReceipt`: [2](#0-1) [3](#0-2) 

confirming that the Ethereum-compatible handler in `api_eth.go` uniquely introduces this unchecked-slice-index pattern.

### Impact Explanation
A crash of the JSON-RPC-serving node process is a concrete denial-of-service impact reachable by any unprivileged, unauthenticated public-RPC caller with no special preconditions beyond knowing/guessing a transaction hash whose receipts slice at the resolved block hash cannot be produced with an entry at `index`. This directly satisfies the "public RPC caller"-reachable, non-resource-only impact bar (process crash / node unavailability), distinct from generic resource exhaustion.

### Likelihood Explanation
The trigger is a single `eth_getTransactionReceipt` RPC call; no privileged access, no p2p/consensus involvement, and no mocked-only path is required. Because `GetBlockReceipts` retrieval is a separate lookup from the transaction/index lookup, any transient condition causing `GetBlockReceipts` to return `nil` or a shorter-than-expected slice for the resolved `blockHash` (e.g., a receipts cache miss combined with a database/backend inconsistency, or receipts pruning/ancient-store edge cases) will panic on the very next line, with no error return or nil-check protecting `receipts[i]`.

### Recommendation
Before entering the `cumulativeGasUsed` loop in `api/api_eth.go`'s `GetTransactionReceipt`, validate that `receipts != nil && uint64(len(receipts)) > index`; if not, return `nil, nil` (transaction/receipt effectively not found) or a proper error, mirroring the defensive nil-check pattern already used elsewhere in this file (e.g., `if block == nil { return nil, errNotFoundBlock }`).

### Proof of Concept
1. As any external RPC client, call `eth_getTransactionReceipt(hash)` for a transaction hash whose lookup succeeds (`tx != nil`, valid `blockHash`/`index`) but for which `GetBlockReceipts(blockHash)` returns `nil` or a slice shorter than `index+1` (e.g., due to a receipts-cache/backing-store inconsistency for that block).
2. The `for i := uint64(0); i <= index; i++ { cumulativeGasUsed += receipts[i].GasUsed }` loop in [4](#0-3)  indexes out of bounds, panicking the RPC-serving goroutine/process and crashing the node — analogous to the CVE's NULL pointer dereference triggered by an unchecked, unpopulated data structure after a lookup step that did not produce the assumed result.

### Citations

**File:** api/api_eth.go (L946-960)
```go
// GetTransactionReceipt returns the transaction receipt for the given transaction hash.
func (api *EthAPI) GetTransactionReceipt(ctx context.Context, hash common.Hash) (map[string]interface{}, error) {
	txpoolAPI := api.kaiaTransactionAPI.b

	// Formats return Kaia transaction Receipt to the Ethereum Transaction Receipt.
	tx, blockHash, blockNumber, index, receipt := txpoolAPI.GetTxLookupInfoAndReceipt(ctx, hash)

	if tx == nil {
		return nil, nil
	}
	receipts := txpoolAPI.GetBlockReceipts(ctx, blockHash)
	cumulativeGasUsed := uint64(0)
	for i := uint64(0); i <= index; i++ {
		cumulativeGasUsed += receipts[i].GasUsed
	}
```

**File:** api/api_kaia_transaction.go (L209-212)
```go
func RpcOutputReceipt(header *types.Header, tx *types.Transaction, blockHash common.Hash, blockNumber uint64, index uint64, receipt *types.Receipt, config *params.ChainConfig) map[string]interface{} {
	if tx == nil || receipt == nil {
		return nil
	}
```

**File:** api/api_kaia_transaction.go (L261-265)
```go
// GetTransactionReceipt returns the transaction receipt for the given transaction hash.
func (s *KaiaTransactionAPI) GetTransactionReceipt(ctx context.Context, hash common.Hash) (map[string]interface{}, error) {
	tx, blockHash, blockNumber, index, receipt := s.b.GetTxLookupInfoAndReceipt(ctx, hash)
	return s.getTransactionReceipt(ctx, tx, blockHash, blockNumber, index, receipt)
}
```
