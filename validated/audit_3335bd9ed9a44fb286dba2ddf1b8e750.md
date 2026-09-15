### Title
Unbounded decode loop in `debug_isGaslessTx` public RPC allows CPU/memory exhaustion via attacker-controlled array - ([File: kaiax/gasless/impl/api.go])

### Summary
The `GaslessAPI.IsGaslessTx` public RPC method decodes every element of an attacker-supplied `rawTxs []hexutil.Bytes` array in an unbounded loop before performing any bound check on the array length, mirroring the "unbounded loop over user-controlled array" pattern from the referenced report (`buy`, `borrow`, `claimAll` loops in kairos-contracts).

### Finding Description
`IsGaslessTx` is exposed as a public JSON-RPC method (`debug` namespace, `Public: true`) reachable by any public-RPC caller without authentication, staking, or payment of gas: [1](#0-0) 

The handler only rejects an empty array up front, then iterates over the entire caller-supplied `rawTxs` slice, performing a byte-prefix check, an `append`, and a full `rlp.DecodeBytes` into a `types.Transaction` for every element, before any check on `len(rawTxs)` against the only two valid cases (1 or 2): [2](#0-1) 

The length constraint (`expected 1 or 2 transactions`) is only enforced at the very end, after the entire decode loop has already executed: [3](#0-2) 

Because this is a query-style RPC call and not a submitted transaction, none of the work performed in the loop is metered by EVM gas or the tx-pool's transaction/size limits (`MaxTxDataSize`) that protect other transaction-decoding paths (e.g. `blockchain/tx_pool.go` validateBlobTx bounding `hashes` by `params.BlobTxMaxBlobs`). An attacker can submit an array with an arbitrarily large number of elements (e.g. hundreds of thousands of small `rawTx` byte strings), each triggering RLP decoding, byte-slice allocation/copy (`append([]byte{...}, rawTx...)`), and `types.Transaction` struct allocation, consuming CPU and memory proportional to the array length with a single RPC call.

### Impact Explanation
A single RPC call with a large `rawTxs` array can consume significant CPU and memory on any node exposing this RPC method publicly, degrading its RPC responsiveness and potentially destabilizing the node process (memory pressure/GC pause) without the attacker paying any gas or fee, unlike the transaction-pool path which enforces per-transaction size and pool-wide costs.

### Likelihood Explanation
Likelihood is limited by two factors: (1) the endpoint sits under the `debug` namespace, and (2) whether it is actually exposed to remote/public RPC callers depends on node operator configuration (many deployments restrict `debug` APIs to local/authenticated access). Where it is exposed, the call requires no special permission, staking, or preconditions — any RPC client can trigger the unbounded loop trivially.

### Recommendation
Validate `len(rawTxs)` against the only supported cases (1 or 2) immediately at the top of `IsGaslessTx`, before entering the decode loop, so that oversized arrays are rejected without doing any decoding work:
```go
if len(rawTxs) == 0 || len(rawTxs) > 2 {
    return ToResponse(fmt.Errorf("expected 1 or 2 transactions, got %d", len(rawTxs)))
}
```
Additionally confirm that `debug_isGaslessTx` is not exposed on untrusted/public RPC endpoints by default, consistent with the general treatment of the `debug` namespace.

### Proof of Concept
1. Enable the `debug` RPC namespace on a target node (or reach a node where it is enabled for public access).
2. Send a `debug_isGaslessTx` JSON-RPC request with `rawTxs` set to an array containing a very large number (e.g. 500,000) of small but syntactically-parseable RLP-encoded transaction byte strings.
3. Observe that the node executes `rlp.DecodeBytes` and allocation for every element in the array — proportional CPU/memory cost — before finally returning the `"expected 1 or 2 transactions, got N"` error, i.e., all the decoding work is wasted and repeatable at will with no cost to the caller.

### Citations

**File:** kaiax/gasless/impl/api.go (L31-40)
```go
func (b *GaslessModule) APIs() []rpc.API {
	return []rpc.API{
		{
			Namespace: "debug",
			Version:   "1.0",
			Service:   NewGaslessAPI(b),
			Public:    true,
		},
	}
}
```

**File:** kaiax/gasless/impl/api.go (L72-96)
```go
func (s *GaslessAPI) IsGaslessTx(ctx context.Context, rawTxs []hexutil.Bytes) *GaslessTxResponse {
	if len(rawTxs) == 0 {
		return ToResponse(errors.New("no transactions provided"))
	}

	// Decode the raw transactions
	txs := make([]*types.Transaction, 0, len(rawTxs))
	for i, rawTx := range rawTxs {
		if len(rawTx) == 0 {
			return ToResponse(fmt.Errorf("empty transaction at index %d", i))
		}

		// Handle Ethereum transaction envelope
		if 0 < rawTx[0] && rawTx[0] < 0x7f {
			rawTx = append([]byte{byte(types.EthereumTxTypeEnvelope)}, rawTx...)
		}

		tx := new(types.Transaction)
		if err := rlp.DecodeBytes(rawTx, tx); err != nil {
			return ToResponse(fmt.Errorf("failed to decode transaction at index %d: %v", i, err))
		}

		txs = append(txs, tx)
	}

```

**File:** kaiax/gasless/impl/api.go (L97-126)
```go
	// Check if the transactions form a valid gasless transaction
	// Case 1: A single swap transaction
	if len(txs) == 1 {
		swapTx := txs[0]
		if !s.b.IsSwapTx(swapTx) {
			return ToResponse(errors.New("transaction is not a swap transaction"))
		}

		return ToResponse(s.b.VerifyExecutable(nil, swapTx))
	}

	// Case 2: An approve transaction followed by a swap transaction
	if len(txs) == 2 {
		approveTx := txs[0]
		swapTx := txs[1]

		if !s.b.IsApproveTx(approveTx) {
			return ToResponse(errors.New("first transaction is not an approve transaction"))
		}

		if !s.b.IsSwapTx(swapTx) {
			return ToResponse(errors.New("second transaction is not a swap transaction"))
		}

		err := s.b.VerifyExecutable(approveTx, swapTx)
		return ToResponse(err)
	}

	return ToResponse(fmt.Errorf("expected 1 or 2 transactions, got %d", len(txs)))
}
```
