### Title
Unbounded storage-key list in `eth_getProof`/`kaia_getProof` allows CPU-intensive Merkle proof generation DoS - ([File: api/api_eth.go])

### Summary
`eth_getProof` (and its `kaia_getProof` alias) accept an unrestricted `storageKeys []string` array from any unauthenticated public-RPC caller. Each key requested triggers a full Merkle-proof trie walk (a `Prove` call, involving hashing at every trie node from leaf to root), with no upper bound on the number of keys, no gas metering, and no per-request cost accounting — directly analogous to ZooKeeper's unmetered `wchp`/`wchc` four-letter commands that let a single client trigger CPU-intensive server-side computation.

### Finding Description
`doGetProof` in [1](#0-0)  iterates over the full, caller-supplied `storageKeys` slice and calls `storageTrie.Prove(...)` for each key without any cap on `len(storageKeys)`. `Prove` in [2](#0-1)  walks the trie from root to leaf, resolving hash nodes and re-hashing every node on the path (`hasher.hashChildren`/`hasher.store` calls per node), which is real CPU-bound work, not I/O-bound.

The two entry points that expose this to any public-RPC caller are:
- `EthAPI.GetProof` at [3](#0-2) 
- `KaiaBlockChainAPI.GetProof` at [4](#0-3) 

Both simply forward the caller-controlled `storageKeys` list to `doGetProof` with no length/size validation, unlike other size-limited paths in the codebase (e.g., the P2P snap-sync handler in `node/cn/snap/handler.go` enforces `req.Bytes`/`hardLimit` byte caps, and `TxPool` enforces `MaxTxDataSize`). There is no equivalent limit for `eth_getProof`/`kaia_getProof`'s storage-key count, nor is this RPC call subject to gas/computation-cost metering the way EVM execution is (see `kaia_estimateComputationCost` / `OpcodeComputationCostLimitInfinite` gating in [5](#0-4)  for contrast — normal EVM calls are cost-capped, but proof generation is not).

A single JSON-RPC request can supply an arbitrarily large `storageKeys` array (bounded only by JSON-RPC message size limits, if any), forcing the node to perform one full trie traversal and re-hash per key, synchronously, on the RPC-serving goroutine. Because a full storage trie can have significant depth, and the caller can pack thousands of keys per request while also issuing many such requests concurrently, this can spike CPU utilization on the queried node, degrading its ability to serve legitimate JSON-RPC requests (transaction submission, `eth_call`, block/tx queries, etc.) — the same "uncontrolled resource consumption" class as CVE-2017-5637.

### Impact Explanation
This does not directly cause fund loss or state divergence, but it is a High-severity availability issue reachable by any unauthenticated public-RPC caller (no on-chain transaction, gas payment, or special privilege required). A sustained flood of `eth_getProof`/`kaia_getProof` calls with large `storageKeys` arrays against an endpoint/public RPC node can starve CPU resources needed for block production/propagation-adjacent RPC services and legitimate transaction submission, effectively denying service to other users of that node — consistent with the "Uncontrolled Resource Consumption" (CWE-400) classification of the reference advisory.

### Likelihood Explanation
Likelihood is high: the API is public, unauthenticated, requires no fee payment, and needs only a single crafted RPC request (with a large `storageKeys` array, potentially against an address with a deep/large storage trie) to trigger disproportionate CPU work relative to request size. No special network position, validator/peer role, or code deployment is required — only access to a public JSON-RPC endpoint, which is explicitly in scope.

### Recommendation
- Enforce a maximum number of entries allowed in `storageKeys` (and/or a maximum total proof-generation cost) in `doGetProof`, rejecting or truncating requests that exceed it, mirroring the byte-size limits already used in `node/cn/snap/handler.go`.
- Consider applying a computation/time budget (similar to `RPCEVMTimeout`) to proof-generation requests so a single request cannot monopolize CPU indefinitely.
- Optionally track and rate-limit heavy `getProof` calls per client/connection, similar to the `heavyAPIRequestCount`/`HeavyAPIRequestLimit` mechanism already used for tracer APIs in `node/cn/tracers/api.go`.

### Proof of Concept
Conceptual PoC (not executed, based on code review):
1. Identify a contract address with a large/deep storage trie on a public Kaia endpoint node.
2. Send a single `eth_getProof` (or `kaia_getProof`) JSON-RPC request with `storageKeys` containing tens of thousands of distinct hex-encoded 32-byte keys.
3. Observe that `doGetProof` performs one `storageTrie.Prove` call per key sequentially and synchronously on the RPC handler goroutine, causing measurable CPU spikes proportional to the number of keys × trie depth.
4. Repeat the request concurrently from multiple connections to amplify CPU exhaustion on the target node, degrading its ability to serve other RPC clients.

Note: I was unable to verify server-side JSON body size limits or per-connection request-size caps in this index (e.g., HTTP/WS payload limits in `networks/rpc`), which would affect the practical maximum size of `storageKeys` per request; a Devin session with full repo/runtime access would be needed to confirm whether any such external limit mitigates this.

### Citations

**File:** api/api_eth.go (L296-351)
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
	state, header, err := b.StateAndHeaderByNumberOrHash(ctx, blockNrOrHash)
	if state == nil || err != nil {
		return nil, err
	}
	codeHash := state.GetCodeHash(address)

	contractStorageRootExt, err := state.GetContractStorageRoot(address)
	if err != nil {
		return nil, err
	}
	contractStorageRoot := contractStorageRootExt.Unextend()

	// if we have a storageTrie, (which means the account exists), we can update the storagehash
	if len(keys) > 0 {
		storageTrie, err := statedb.NewTrie(contractStorageRoot, state.Database().TrieDB(), nil)
		if err != nil {
			return nil, err
		}
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

**File:** api/api_kaia_blockchain.go (L596-609)
```go
	// Create a helper to check if a gas allowance results in an executable transaction
	executable := func(gas uint64) (bool, *blockchain.ExecutionResult, error) {
		args.Gas = (*hexutil.Uint64)(&gas)
		result, _, err := DoCall(ctx, b, args, blockNrOrHash, vm.Config{ComputationCostLimit: params.OpcodeComputationCostLimitInfinite}, timeout, gasCap)
		if err != nil {
			if errors.Is(err, blockchain.ErrIntrinsicGas) || errors.Is(err, blockchain.ErrFloorDataGas) {
				return true, nil, nil // Special case, raise gas limit
			}
			return true, nil, err // Bail out
		}
		return result.Failed(), result, nil
	}

	return blockchain.DoEstimateGas(ctx, gasLimit, gasCap.Uint64(), args.Value.ToInt(), feeCap, balance, executable)
```

**File:** api/api_kaia_blockchain.go (L637-639)
```go
func (s *KaiaBlockChainAPI) GetProof(ctx context.Context, address common.Address, storageKeys []string, blockNrOrHash rpc.BlockNumberOrHash) (*EthAccountResult, error) {
	return doGetProof(ctx, s.b, address, storageKeys, blockNrOrHash)
}
```
