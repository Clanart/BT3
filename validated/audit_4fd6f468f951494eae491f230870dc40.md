### Title
Unbounded, non-deduplicated `storageKeys` in `eth_getProof`/`kaia_getProof` causes per-request trie-proof amplification - (File: api/api_eth.go)

### Summary
`doGetProof` in `api/api_eth.go` builds a Merkle proof for every entry in the caller-supplied `storageKeys` array without any cap on array length and without deduplicating repeated keys. Each entry triggers a full `storageTrie.Prove()` walk from the trie root down to the leaf. This is directly analogous to the reported PocketMine-MP bug: a client-controlled list is processed item-by-item with no uniqueness/size check, and each item causes disproportionate server-side work.

### Finding Description
`doGetProof` allocates `keys`, `keyLengths`, and `storageProof` sized to `len(storageKeys)` with no upper bound, then iterates every key and calls `storageTrie.Prove(crypto.Keccak256(key.Bytes()), 0, &proof)` for each one: [1](#0-0) [2](#0-1) 

There is no limit on `len(storageKeys)` and no deduplication of repeated hex strings before the proof loop runs — unlike the JSON-RPC batch layer, which does enforce `BatchRequestLimit`/`BatchResponseMaxSize` per batch of top-level calls: [3](#0-2) [4](#0-3) 

Those limits only bound the number of top-level JSON-RPC calls in a batch; they do not bound the size of a *single* call's array parameter such as `storageKeys` in one `eth_getProof`/`kaia_getProof` invocation. `GetProof` on both `EthAPI` and `KaiaBlockChainAPI` forward directly into `doGetProof` with no additional validation: [5](#0-4) [6](#0-5) 

Compare this to `ServiceGetByteCodesQuery`, `ServiceGetAccountRangeQuery`, and `ServiceGetStorageRangesQuery` in the p2p snap-sync handler, which explicitly cap counts/bytes (`maxCodeLookups`, `softResponseLimit`, `hardLimit`) before doing trie work — precisely the missing control in `doGetProof`: [7](#0-6) 

Each `Prove` call performs disk/cache-backed trie node reads proportional to trie depth and re-encodes proof nodes into the response (`proofList.Put` appends hex strings). A caller can request thousands of duplicate or distinct storage keys in one `eth_getProof` call, forcing the node to perform thousands of independent trie traversals and build a correspondingly large in-memory response for a single RPC call — with no gas payment, no rate limiting specific to this call, and no per-call array size cap.

### Impact Explanation
This is reachable by any unauthenticated public RPC caller (anyone able to reach `eth_getProof`/`kaia_getProof`, which are enabled by default per `api/README.md`). A single request with a large or highly duplicated `storageKeys` array can consume disproportionate CPU (repeated trie descents) and memory (accumulated proof node byte slices) on the serving node, degrading or denying RPC service — the same "one client input list, unmetered per-item expansion" pattern as the PocketMine-MP `ResourcePackDataInfoPacket` amplification bug. Because this only affects the RPC-serving node's own resource usage and does not directly cause unauthorized value movement, supply inflation, or state divergence between honest nodes, it is a resource-exhaustion (DoS) class issue rather than a consensus-breaking one.

### Likelihood Explanation
High likelihood of triggering: the API is public, requires no special privilege or gas payment, and the request shape (a plain array of hex strings) is trivial to construct at scale. No dedup or per-call size cap exists in the code path examined.

### Recommendation
In `doGetProof` (`api/api_eth.go`), before processing `storageKeys`:
1. Enforce a maximum number of storage keys per `eth_getProof`/`kaia_getProof` call (configurable, similar in spirit to `maxCodeLookups`/`softResponseLimit` in the snap-sync handler).
2. Deduplicate `storageKeys` (e.g., via a `map[common.Hash]struct{}`) so repeated keys reuse a single `Prove()` result instead of re-walking the trie.
3. Consider accounting the proof-generation cost into existing RPC resource controls (e.g., `HeavyDebugRequestLimitFlag`/timeout enforcement) so a single expensive `getProof` call cannot monopolize node resources.

### Proof of Concept
Send a single JSON-RPC call:
```json
{"jsonrpc":"2.0","method":"eth_getProof","params":["0x0000000000000000000000000000000000000000", ["0x00","0x00","0x00", "...repeated thousands of times or thousands of distinct keys..."], "latest"],"id":1}
```
This single call is not blocked by `RPCBatchRequestLimit`/`RPCBatchResponseMaxSize` (those apply to top-level JSON-RPC batch arrays, not to the `storageKeys` parameter of one call) and causes `doGetProof` to execute one `storageTrie.Prove()` per array element, proportional CPU/memory cost per call, with no dedup or cap in the reviewed code.

### Citations

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

**File:** api/api_eth.go (L322-350)
```go
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
```

**File:** api/api_eth.go (L374-377)
```go
// GetProof returns the Merkle-proof for a given account and optionally some storage keys
func (api *EthAPI) GetProof(ctx context.Context, address common.Address, storageKeys []string, blockNrOrHash rpc.BlockNumberOrHash) (*EthAccountResult, error) {
	return doGetProof(ctx, api.kaiaBlockChainAPI.b, address, storageKeys, blockNrOrHash)
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

**File:** api/api_kaia_blockchain.go (L637-639)
```go
func (s *KaiaBlockChainAPI) GetProof(ctx context.Context, address common.Address, storageKeys []string, blockNrOrHash rpc.BlockNumberOrHash) (*EthAccountResult, error) {
	return doGetProof(ctx, s.b, address, storageKeys, blockNrOrHash)
}
```

**File:** node/cn/snap/handler.go (L411-437)
```go
// ServiceGetByteCodesQuery assembles the response to a byte codes query.
// It is exposed to allow external packages to test protocol behavior.
func ServiceGetByteCodesQuery(chain SnapshotReader, req *GetByteCodesPacket) [][]byte {
	if req.Bytes > softResponseLimit {
		req.Bytes = softResponseLimit
	}
	if len(req.Hashes) > maxCodeLookups {
		req.Hashes = req.Hashes[:maxCodeLookups]
	}
	// Retrieve bytecodes until the packet size limit is reached
	var (
		codes [][]byte
		bytes uint64
	)
	for _, hash := range req.Hashes {
		if hash == types.EmptyCodeHash {
			// Peers should not request the empty code, but if they do, at
			// least sent them back a correct response without db lookups
			codes = append(codes, []byte{})
		} else if blob, err := chain.ContractCode(hash); err == nil {
			codes = append(codes, blob)
			bytes += uint64(len(blob))
		}
		if bytes > req.Bytes {
			break
		}
	}
```
