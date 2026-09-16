### Title
Unbounded storage key count in `eth_getProof`/`kaia_getProof` allows RPC-triggered CPU/memory exhaustion via unlimited Merkle proof generation - ([File: api/api_eth.go])

### Summary
`doGetProof` in `api/api_eth.go` accepts a caller-supplied `storageKeys []string` array with no upper bound on its length, and generates a full Merkle proof (trie walk) for every single key in the request. A single public JSON-RPC call to `eth_getProof` (or `kaia_getProof` via `KaiaBlockChainAPI.GetProof`) can therefore trigger an arbitrarily large number of expensive trie traversals and proof-node allocations in one request, mirroring the Rack `Range` header bug class where the number of "ranges" (small individually-cheap units) is unbounded even though other size-based protections exist.

### Finding Description
`doGetProof` allocates parallel slices sized to `len(storageKeys)` and then, for each key, calls `storageTrie.Prove(...)`: [1](#0-0) [2](#0-1) 

Each `Prove` call walks the trie from the root, collecting and re-hashing every node on the path and copying encoded node bytes into the response (`proofList`): [3](#0-2) 

There is no check anywhere in `doGetProof`, `EthAPI.GetProof`, or `KaiaBlockChainAPI.GetProof` limiting the number of entries in `storageKeys`: [4](#0-3) [5](#0-4) 

The only length restriction present is `decodeHash`, which limits each *individual* key string to 32 bytes — analogous to the total-byte-size check in Rack's CVE-2024-26141 fix — but it does not limit the *count* of keys, exactly the gap exploited by CVE-2026-34826 (`bytes=0-0,0-0,...`). [6](#0-5) 

Unlike `KaiaBlockChainAPI.GetBlockWithConsensusInfoByNumberRange`, which explicitly caps the number of items processed per call (`maxConsensusInfoBlocks`), no equivalent cap exists for `GetProof`'s storage key array: [7](#0-6) 

Generic RPC-level protections (`batchRequestLimit`, `batchResponseMaxSize`, `pendingRequestLimit`) only bound the number of JSON-RPC batch calls and cumulative *batch* response size; they do not bound the size of parameters inside a single call, so they do not mitigate a single oversized `storageKeys` array: [8](#0-7) 

### Impact Explanation
An unauthenticated public-RPC caller can submit one `eth_getProof` request with a very large `storageKeys` array (e.g., tens of thousands of entries). Each entry forces a full trie walk plus per-node hashing/encoding and response buffer growth, so the cost scales linearly (or worse, due to allocation/GC pressure) with the number of supplied keys while the request itself is small. This can consume disproportionate CPU, memory, and I/O on the node, degrading or denying service to other RPC clients — the same "many small overlapping/duplicate units, disproportionate cost" pattern as the Rack advisory. This falls under CWE-400/CWE-770 resource exhaustion, matching the Medium severity class of the reported advisory.

### Likelihood Explanation
`eth_getProof` is a standard, commonly-enabled read-only JSON-RPC method with no special permissions, reachable by any public-RPC caller without needing to submit a transaction or hold funds. Constructing a request with many storage key strings requires no special knowledge (any 32-byte hex value works, duplicates are fine), making exploitation straightforward once the endpoint is publicly exposed.

### Recommendation
Impose an explicit maximum on the number of entries accepted in `storageKeys` (and reject or truncate requests exceeding it), similar to the `maxConsensusInfoBlocks` pattern already used for `GetBlockWithConsensusInfoByNumberRange`. Additionally, consider applying a request-level timeout/deadline (similar to `GetLogsDeadline`/`GetLogsMaxItems` used in the filter API) around per-call proof generation so that even legitimately large requests cannot monopolize node resources.

### Proof of Concept
Send a single `eth_getProof` request with an artificially large `storageKeys` array, e.g.:
```json
{
  "jsonrpc":"2.0","id":1,"method":"eth_getProof",
  "params":[
    "0x0000000000000000000000000000000000000000",
    ["0x0","0x0","0x0", ... /* repeat tens of thousands of times */],
    "latest"
  ]
}
```
Each `"0x0"` entry causes `doGetProof` to run `storageTrie.Prove` once, so a single HTTP request with a large enough array can be crafted to consume significant CPU/memory on the node before returning a response, without hitting `batchRequestLimit` or `batchResponseMaxSize` (which apply to batched JSON-RPC arrays, not to array-typed parameters within one call).

### Citations

**File:** api/api_eth.go (L279-294)
```go
func decodeHash(s string) (h common.Hash, inputLength int, err error) {
	if strings.HasPrefix(s, "0x") || strings.HasPrefix(s, "0X") {
		s = s[2:]
	}
	if (len(s) & 1) > 0 {
		s = "0" + s
	}
	if len(s) > 64 {
		return common.Hash{}, len(s) / 2, errors.New("hex string too long, want at most 32 bytes")
	}
	b, err := hex.DecodeString(s)
	if err != nil {
		return common.Hash{}, 0, errors.New("hex string invalid")
	}
	return common.BytesToHash(b), len(b), nil
}
```

**File:** api/api_eth.go (L296-309)
```go
func doGetProof(ctx context.Context, b Backend, address common.Address, storageKeys []string, blockNrOrHash rpc.BlockNumberOrHash) (*EthAccountResult, error) {
	var (
		keys         = make([]common.Hash, len(storageKeys))
		keyLengths   = make([]int, len(storageKeys))
		storageProof = make([]EthStorageResult, len(storageKeys))
	)
	// Deserialize all keys. This prevents state access on invalid input.
	for i, hexKey := range storageKeys {
		var err error
		keys[i], keyLengths[i], err = decodeHash(hexKey)
		if err != nil {
			return nil, err
		}
	}
```

**File:** api/api_eth.go (L328-350)
```go
		// Create the proofs for the storageKeys.
		for i, key := range keys {
			// Output key encoding is a bit special: if the input was a 32-byte hash, it is
			// returned as such. Otherwise, we apply the QUANTITY encoding mandated by the
			// JSON-RPC spec for getProof. This behavior exists to preserve backwards
			// compatibility with older client versions.
			var outputKey string
			if keyLengths[i] != 32 {
				outputKey = hexutil.EncodeBig(key.Big())
			} else {
				outputKey = hexutil.Encode(key[:])
			}
			if storageTrie == nil {
				storageProof[i] = EthStorageResult{outputKey, &hexutil.Big{}, []string{}}
				continue
			}
			var proof proofList
			if err := storageTrie.Prove(crypto.Keccak256(key.Bytes()), 0, &proof); err != nil {
				return nil, err
			}
			value := (*hexutil.Big)(state.GetState(address, key).Big())
			storageProof[i] = EthStorageResult{outputKey, value, proof}
		}
```

**File:** api/api_eth.go (L374-377)
```go
// GetProof returns the Merkle-proof for a given account and optionally some storage keys
func (api *EthAPI) GetProof(ctx context.Context, address common.Address, storageKeys []string, blockNrOrHash rpc.BlockNumberOrHash) (*EthAccountResult, error) {
	return doGetProof(ctx, api.kaiaBlockChainAPI.b, address, storageKeys, blockNrOrHash)
}
```

**File:** storage/statedb/proof.go (L50-106)
```go
func (t *Trie) Prove(key []byte, fromLevel uint, proofDB ProofDBWriter) error {
	// Collect all nodes on the path to key.
	key = keybytesToHex(key)
	nodes := []node{}
	tn := t.root
	for len(key) > 0 && tn != nil {
		switch n := tn.(type) {
		case *shortNode:
			if len(key) < len(n.Key) || !bytes.Equal(n.Key, key[:len(n.Key)]) {
				// The trie doesn't contain the key.
				tn = nil
			} else {
				tn = n.Val
				key = key[len(n.Key):]
			}
			nodes = append(nodes, n)
		case *fullNode:
			tn = n.Children[key[0]]
			key = key[1:]
			nodes = append(nodes, n)
		case hashNode:
			var err error
			tn, err = t.resolveHash(n, nil)
			if err != nil {
				logger.Error(fmt.Sprintf("Unhandled trie error: %v", err))
				return err
			}
		default:
			panic(fmt.Sprintf("%T: invalid node: %v", tn, tn))
		}
	}
	hasher := newHasher(nil)
	defer returnHasherToPool(hasher)

	for i, n := range nodes {
		// Don't bother checking for errors here since hasher panics
		// if encoding doesn't work and we're not writing to any database.
		n, _ = hasher.hashChildren(n, nil, false)
		hn, _ := hasher.store(n, nil, false, false)
		if hash, ok := hn.(hashNode); ok || i == 0 {
			// If the node's database encoding is a hash (or is the
			// root node), it becomes a proof element.
			if fromLevel > 0 {
				fromLevel--
			} else {
				// hash is for the merkle proof. hash = Keccak(rlp.Encode(nodeForHashing(n)))
				enc, _ := rlp.EncodeToBytes(hasher.nodeForHashing(n))
				if !ok {
					hash = hasher.hashData(enc, false)
				}
				dbKey := database.TrieNodeKey(common.BytesToExtHash(hash))
				proofDB.WriteMerkleProof(dbKey, enc)
			}
		}
	}
	return nil
}
```

**File:** api/api_kaia_blockchain.go (L305-335)
```go
func (s *KaiaBlockChainAPI) GetBlockWithConsensusInfoByNumberRange(ctx context.Context, start *rpc.BlockNumber, end *rpc.BlockNumber) (map[string]interface{}, error) {
	blocks := make(map[string]interface{})

	if start == nil || end == nil {
		logger.Trace("the range values should not be nil.", "start", start, "end", end)
		return nil, errRangeNil
	}

	// check error status.
	startNum := start.Int64()
	endNum := end.Int64()
	if startNum < 0 {
		logger.Trace("start should be positive", "start", startNum)
		return nil, errStartNotPositive
	}

	eChain := s.b.CurrentBlock().Number().Int64()
	if endNum > eChain {
		logger.Trace("end should be smaller than the latest block number", "end", end, "eChain", eChain)
		return nil, errEndLargetThanLatest
	}

	if startNum > endNum {
		logger.Trace("start should be smaller than end", "start", startNum, "end", endNum)
		return nil, errStartLargerThanEnd
	}

	if endNum-startNum+1 > maxConsensusInfoBlocks {
		logger.Trace("too many blocks requested", "start", startNum, "end", endNum, "max", maxConsensusInfoBlocks)
		return nil, errRequestedBlocksTooLarge
	}
```

**File:** api/api_kaia_blockchain.go (L637-639)
```go
func (s *KaiaBlockChainAPI) GetProof(ctx context.Context, address common.Address, storageKeys []string, blockNrOrHash rpc.BlockNumberOrHash) (*EthAccountResult, error) {
	return doGetProof(ctx, s.b, address, storageKeys, blockNrOrHash)
}
```

**File:** networks/rpc/handler.go (L104-163)
```go
func (h *handler) handleBatch(msgs []*jsonrpcMessage) {
	// Emit error response for empty batches:
	if len(msgs) == 0 {
		rpcErrorResponsesCounter.Inc(1)
		h.startCallProc(func(cp *callProc) {
			h.conn.writeJSON(cp.ctx, errorMessage(&invalidRequestError{"empty batch"}))
		})
		return
	}

	// Apply per-batch item limit.
	if h.batchRequestLimit != 0 && len(msgs) > h.batchRequestLimit {
		rpcErrorResponsesCounter.Inc(int64(len(msgs)))
		h.startCallProc(func(cp *callProc) {
			h.respondWithBatchTooLarge(cp, msgs)
		})
		return
	}

	rpcTotalRequestsCounter.Inc(int64(len(msgs)))

	// Handle non-call messages first:
	calls := make([]*jsonrpcMessage, 0, len(msgs))
	for _, msg := range msgs {
		if handled := h.handleImmediate(msg); !handled {
			calls = append(calls, msg)
		}
	}
	if len(calls) == 0 {
		return
	}

	if atomic.LoadInt64(&pendingRequestCount) > pendingRequestLimit {
		rpcErrorResponsesCounter.Inc(int64(len(calls)))
		err := &invalidRequestError{"server requests exceed the limit"}
		logger.Debug(fmt.Sprintf("request error %v\n", err))
		h.startCallProc(func(cp *callProc) {
			h.conn.writeJSON(cp.ctx, errorMessage(err))
		})
		return
	}

	// Process calls on a goroutine because they may block indefinitely:
	h.startCallProc(func(cp *callProc) {
		answers := make([]*jsonrpcMessage, 0, len(calls))
		responseBytes := 0
		tooLargeAt := -1
		for i, msg := range calls {
			answer := h.handleCallMsg(cp, msg)
			if answer != nil {
				answers = append(answers, answer)
			}
			if h.batchResponseMaxSize != 0 && answer != nil {
				responseBytes += len(answer.Result)
				if responseBytes > h.batchResponseMaxSize {
					tooLargeAt = i + 1
					break
				}
			}
		}
```
