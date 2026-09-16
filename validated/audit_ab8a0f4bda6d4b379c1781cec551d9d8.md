### Title
`kaia_sendRawTransactions` fans out an unbounded raw-transaction list into unbounded per-tx pool submissions - ([File: api/api_kaia_transaction.go])

### Summary
`KaiaTransactionAPI.SendRawTransactions` accepts a `[]hexutil.Bytes` parameter with no upper bound on its length, and expands it into one RLP-decode, one `types.Transaction` allocation, and one `s.b.SendTx()` submission per element before any aggregate per-request budget is enforced.

### Finding Description
`SendRawTransactions` iterates the caller-supplied `inputs` slice and, for every element, RLP-decodes it into a `*types.Transaction` and calls `s.b.SendTx(ctx, tx)`: [1](#0-0) 

There is no check on `len(inputs)` beyond rejecting the empty-slice case; the only bound applied is the JSON-RPC layer's `BatchRequestLimit`, which caps the number of *top-level JSON-RPC batch calls* in a single HTTP/WS payload: [2](#0-1) 

That limit is orthogonal to this bug: it does not bound the size of the `inputs` array *parameter* inside a single `kaia_sendRawTransactions` call. A single JSON-RPC call (one entry, well under `BatchRequestLimit`) can carry an `inputs` array with tens of thousands of raw-tx hex strings, and each one drives a full RLP decode, transaction struct allocation, signature-derived sender recovery, and a `SendTx` call into the transaction pool - all before the pool's own per-account/global slot capacity checks (`ExecSlotsAll`/`NonExecSlotsAll` in `blockchain/tx_pool.go`) can reject overflow, because those checks are only exercised inside pool admission (`AddRemote`/`addTx`), not before the loop starts: [3](#0-2) 

Contrast this to `checkAndAddTxs`, which is used elsewhere for batch tx-pool insertion and pre-computes `poolCapacity` before doing per-tx work and truncates the input slice to fit: [4](#0-3) 
`SendRawTransactions` bypasses this batching-aware path entirely and calls `SendTx` (single-tx path) once per array element without ever consulting pool capacity before starting the loop.

The overall shape mirrors the vLLM report precisely: an outer, attacker-controlled list parameter (`prompt` there, `inputs` here) is expanded 1:1 into backend work units (engine generators there, tx-pool submissions/decodes here) with no outer-list-length bound enforced before the per-element fan-out begins.

### Impact Explanation
An authenticated (or, if `kaia_sendRawTransactions` is exposed without additional gating, any) RPC caller can submit one HTTP/WS request whose `inputs` array has an attacker-chosen size, forcing the node to perform CPU-bound RLP decoding, ECDSA-based sender recovery, and tx-pool admission attempts proportional to that size — all within a single RPC call's synchronous execution, before existing tx-pool capacity/pricing limits can throttle anything. This can starve node resources (CPU, goroutine/RPC worker time, tx-pool locks) shared by other RPC callers and by consensus-critical mining/verification paths, which is a resource-exhaustion / availability impact on a public-RPC-reachable surface — matching the CWE-400 classification of the source report.

### Likelihood Explanation
The endpoint is a plain public JSON-RPC method (`kaia_sendRawTransactions`) reachable by any client with RPC access; it requires no special privilege beyond calling the RPC endpoint, and the payload (an array of raw tx bytes, which need not even be valid — malformed/undecodable entries still cost an RLP-decode attempt and error handling) is trivial to construct. The only constraint is the HTTP/WS server's overall request body-size limit (`MaxRequestContentLength`), which still allows tens of thousands of small raw-tx entries in one call.

### Recommendation
Add an explicit upper bound on `len(inputs)` in `SendRawTransactions` (and any structurally similar batched-array API) and reject the request outright before doing any per-element RLP decode or `SendTx` call, analogous to `checkAndAddTxs`'s capacity pre-check in `blockchain/tx_pool.go`. Consider making the limit configurable in the same style as `RPCBatchRequestLimit`, and route this API through the pool's batch-aware `AddRemotes`/`checkAndAddTxs` path instead of iterating single-tx `SendTx` calls, so pool capacity is checked before decoding/validating the full array.

### Proof of Concept
1. Start a Kaia node with `kaia_sendRawTransactions` RPC method enabled.
2. Send a single JSON-RPC request (one call, not a JSON-RPC batch, so `BatchRequestLimit` never triggers):
```json
{
  "jsonrpc": "2.0",
  "method": "kaia_sendRawTransactions",
  "params": [[
     "0x<rawtx1>", "0x<rawtx2>", "...", "0x<rawtxN>"
  ]],
  "id": 1
}
```
3. Set `N` to tens of thousands of entries (valid or intentionally malformed raw tx bytes, small each, staying under `MaxRequestContentLength`).
4. Observe the server synchronously RLP-decodes and attempts `SendTx` for every one of the `N` entries in the single call in `api/api_kaia_transaction.go:392-427`, consuming CPU/time proportional to `N` with no pre-check rejecting the oversized array, unlike JSON-RPC-batch-level protections in `networks/rpc/handler.go:114-121` which do not apply to this in-parameter array.

### Citations

**File:** api/api_kaia_transaction.go (L392-427)
```go
// SendRawTransactions will add multiple signed transactions to the transaction pool.
func (s *KaiaTransactionAPI) SendRawTransactions(ctx context.Context, inputs []hexutil.Bytes) ([]common.Hash, error) {
	hash := []common.Hash{}
	errs := []error{}

	if len(inputs) == 0 {
		hash = append(hash, common.Hash{})
		return hash, errors.New("Empty input")
	}

	for i, input := range inputs {
		if len(input) == 0 {
			hash = append(hash, common.Hash{})
			errs = append(errs, fmt.Errorf("Index %d: empty input", i))
			break
		}
		// Allow naked Ethereum tx types
		if 0 < input[0] && input[0] < 0x7f {
			input = append([]byte{byte(types.EthereumTxTypeEnvelope)}, input...)
		}
		tx := new(types.Transaction)
		if err := rlp.DecodeBytes(input, tx); err != nil {
			hash = append(hash, common.Hash{})
			errs = append(errs, fmt.Errorf("Index %d: %w", i, err))
			break
		}
		if err := s.b.SendTx(ctx, tx); err != nil {
			hash = append(hash, common.Hash{})
			errs = append(errs, fmt.Errorf("Index %d: %w", i, err))
			break
		}
		hash = append(hash, tx.Hash())
	}

	return hash, errors.Join(errs...)
}
```

**File:** networks/rpc/handler.go (L114-121)
```go
	// Apply per-batch item limit.
	if h.batchRequestLimit != 0 && len(msgs) > h.batchRequestLimit {
		rpcErrorResponsesCounter.Inc(int64(len(msgs)))
		h.startCallProc(func(cp *callProc) {
			h.respondWithBatchTooLarge(cp, msgs)
		})
		return
	}
```

**File:** blockchain/tx_pool.go (L1493-1498)
```go
// AddRemote enqueues a single transaction into the pool if it is valid. If the
// sender is not among the locally tracked ones, full pricing constraints will
// apply.
func (pool *TxPool) AddRemote(tx *types.Transaction) error {
	return pool.addTx(tx, false)
}
```

**File:** blockchain/tx_pool.go (L1514-1541)
```go
// checkAndAddTxs compares the size of given transactions and the capacity of TxPool.
// If given transactions exceed the capacity of TxPool, it slices the given transactions
// so it can fit into TxPool's capacity.
func (pool *TxPool) checkAndAddTxs(txs []*types.Transaction, local bool) []error {
	// Single-tx fast path: bypass the slot pre-check so add()'s existing
	// missing-nonce admission can run even when the pool is at cap.
	if len(txs) == 1 {
		return []error{pool.addTx(txs[0], local)}
	}

	poolSize := uint64(pool.all.Count())
	poolCapacity := int(pool.config.ExecSlotsAll + pool.config.NonExecSlotsAll - poolSize)
	numTxs := len(txs)

	if poolCapacity < numTxs {
		txs = txs[:poolCapacity]
	}

	errs := pool.addTxs(txs, local)

	if poolCapacity < numTxs {
		for i := 0; i < numTxs-poolCapacity; i++ {
			errs = append(errs, ErrTxPoolOverflow)
		}
	}

	return errs
}
```
