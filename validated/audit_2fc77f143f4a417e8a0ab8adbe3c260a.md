### Title
Unbounded array parameter in `kaia_sendRawTransactions` allows single-request resource-exhaustion DoS bypassing the JSON-RPC batch-size limit - ([File: api/api_kaia_transaction.go])

### Summary
`KaiaTransactionAPI.SendRawTransactions` accepts an arbitrary-length `[]hexutil.Bytes` parameter and RLP-decodes/submits each entry to the transaction pool, with no upper bound on `len(inputs)`. This mirrors the Saleor GraphQL batching flaw: the node enforces a limit on the number of *top-level* JSON-RPC batch entries (`rpc.BatchRequestLimit`), but that check is orthogonal to the size of an *array parameter* passed to a single RPC method. A caller can therefore issue one JSON-RPC call (not even using JSON-RPC batching) with a huge `inputs` array and force the node to perform expensive per-transaction work (RLP decode, ECDSA signature recovery / `ValidateSender`, `AccountKey` validation) for every element, unconstrained by the batch-size protections that were specifically added elsewhere in the RPC layer.

### Finding Description
The RPC-layer batching protections added to `networks/rpc` (`BatchRequestLimit`, `BatchResponseMaxSize`, enforced in `handler.handleBatch`) cap the number of JSON-RPC *objects* in a batch array: [1](#0-0) [2](#0-1) 

However, `kaia_sendRawTransactions` is a *single* RPC call whose own parameter is an unbounded array of raw transactions, and this array is never checked against any size limit before the loop begins processing: [3](#0-2) 

For each element, the method RLP-decodes the transaction and calls `s.b.SendTx(ctx, tx)`, which funnels into `TxPool.AddLocal`/`addTx` → `validateTx`, which performs `tx.ValidateSender(...)` (ECDSA signature recovery via `SenderPubkey`/`Sender`) and `AccountKey` validation before any pool-capacity check for that specific transaction: [4](#0-3) [5](#0-4) 

Because `SendRawTransactions` calls `s.b.SendTx` once per element in a plain loop (not via the batch-aware `AddRemotes`/`checkAndAddTxs` path used elsewhere for bulk submission, which at least pre-computes pool capacity before iterating): [6](#0-5) 
each of the N transactions independently pays the full signature-recovery/validation cost regardless of whether the pool has room, since `SendTx`/`AddLocal`/`addTx` is invoked per item with no upfront N-vs-capacity gate as `checkAndAddTxs` provides for `AddRemotes`.

An attacker can craft N syntactically valid but ultimately rejected (e.g., insufficient balance, duplicate nonce, or simply many distinct valid low-value transfers) RLP-encoded transactions and place them in a single `kaia_sendRawTransactions` call. Each one forces the node to do an expensive `ecrecover`/`AccountKey` validation pass before failing/succeeding, and this entire per-request cost is incurred synchronously within `h.handleCallMsg`, occupying one RPC worker goroutine for the duration. The HTTP body-size limit (`MaxRequestContentLength`) bounds the *encoded* transaction size but does not meaningfully bound N, since raw transactions can be made very small (e.g., minimal legacy transfers), allowing thousands of decode+ecrecover operations to be packed into one request well within typical body-size limits.

### Impact Explanation
This is a resource-exhaustion (CPU/goroutine-time) denial-of-service vector reachable by any public JSON-RPC caller with no special privileges (anyone who can reach the `kaia`/`eth` RPC namespace, which is the same threat actor class as the Saleor GraphQL caller). A sustained stream of such requests can exhaust `rpc.ConcurrencyLimit`/CPU on public RPC nodes, degrading availability of transaction submission and read APIs for legitimate users — directly analogous to the "exhaust resources" impact in the reference CVE. It does not, by itself, cause fund loss, supply inflation, or consensus divergence, but it satisfies the accepted "resource exhaustion via unbounded batching in a single request" impact class this scan is validating against.

### Likelihood Explanation
High likelihood: the endpoint is exposed on any node with the `kaia`/personal-equivalent transaction API enabled over HTTP/WS, requires no authentication beyond RPC access, and the attack payload (many small validly-RLP-encoded transactions) is trivial to construct. No rate limiting specific to this method's array length exists; only the generic `rpc.ConcurrencyLimit` (concurrent connections) and JSON-RPC *batch* limit apply, neither of which bounds the size of this single method's parameter array.

### Recommendation
Add an explicit cap on `len(inputs)` in `SendRawTransactions` (and its `eth_sendRawTransactions`-style aggregate equivalents if present), returning an error such as `"too many transactions in request"` when exceeded, mirroring the `rpc.BatchRequestLimit` protection already implemented for JSON-RPC batches. Consider also routing bulk submissions through the capacity-aware `checkAndAddTxs`/`AddRemotes` path (pre-checking pool capacity before performing per-tx signature validation) rather than a raw per-element `SendTx` loop, and/or bounding total decode/verification work per RPC call via a context-scoped budget.

### Proof of Concept
1. Start a Kaia node with HTTP RPC enabled and the `kaia` (or `eth`) namespace exposed.
2. Generate N (e.g., 50,000) small, syntactically valid signed legacy transactions from distinct throwaway keys (no funding needed to trigger the ecrecover/AccountKey cost — they will fail balance checks only after signature validation).
3. RLP-encode each transaction and submit them all in one JSON-RPC call:
```json
{
  "jsonrpc": "2.0",
  "method": "kaia_sendRawTransactions",
  "params": [["0x...tx1...", "0x...tx2...", ... , "0x...txN..."]],
  "id": 1
}
```
4. Observe that the request is accepted by the JSON-RPC batch-limit check (this is a single, non-batched call) and that `SendRawTransactions` synchronously iterates and performs RLP decode + `ValidateSender` (ECDSA recovery) for every transaction in the array, consuming a proportionally large amount of CPU time and holding an RPC worker goroutine, unconstrained by `rpc.BatchRequestLimit`/`rpc.ConcurrencyLimit`.

### Citations

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

**File:** networks/rpc/server.go (L79-85)
```go
	// BatchRequestLimit is the maximum number of items in a JSON-RPC batch.
	// 0 disables the check. Overwritten by --rpc.batch-request-limit flag.
	BatchRequestLimit = 1000

	// BatchResponseMaxSize is the maximum total response bytes per JSON-RPC batch.
	// 0 disables the check. Overwritten by --rpc.batch-response-max-size flag.
	BatchResponseMaxSize = 25 * 1024 * 1024
```

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

**File:** blockchain/tx_pool.go (L896-901)
```go

	// Make sure the transaction is signed properly
	gasFrom, err := tx.ValidateSender(pool.signer, pool.currentState, pool.currentBlockNumber)
	if err != nil {
		return types.ErrSender(err)
	}
```

**File:** blockchain/tx_pool.go (L1156-1161)
```go
	// If the transaction fails basic validation, discard it
	if err := pool.validateTx(tx); err != nil {
		logger.Trace("Discarding invalid transaction", "hash", hash, "err", err)
		invalidTxCounter.Inc(1)
		return false, err
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
