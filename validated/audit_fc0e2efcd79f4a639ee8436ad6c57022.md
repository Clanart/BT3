### Title
Unbounded `storageKeys` parameter in `eth_getProof`/`kaia_getProof` allows single-call resource exhaustion - ([File: api/api_eth.go])

### Summary
The public JSON-RPC method `eth_getProof` (and its `kaia_getProof` alias) accepts a caller-supplied `storageKeys []string` array with no length limit before allocating per-key structures and generating a Merkle proof for each key against the live state trie.

### Finding Description
`doGetProof` allocates slices sized directly by the untrusted `storageKeys` input and then, for every key, opens/queries the storage trie and walks the path from root to leaf, writing every visited node into a `proofList`: [1](#0-0) [2](#0-1) 

There is no check anywhere in this path (or in the `KaiaBlockChainAPI.GetProof` wrapper that calls the same `doGetProof`) that caps `len(storageKeys)`: [3](#0-2) 

Each entry triggers `storageTrie.Prove()`, which walks and re-hashes every node from the trie root to the target key, doing hashing (`newHasher`, `hashChildren`), RLP encoding, and DB reads for uncached nodes: [4](#0-3) 

A caller can submit a single `eth_getProof` request with an arbitrarily large `storageKeys` array (e.g., tens of thousands of entries), forcing the node to perform that many independent trie traversals/hashing operations and to build a correspondingly large in-memory/JSON response, all within one RPC call — with no gas cost, no batch/array size limit, and no rate limiting specific to this parameter found in the codebase. This directly mirrors CVE-2023-6841's root cause: an object attribute list (here, the storage-key list of the "get proof" request) has no bound, so response construction cost scales linearly (or worse, with trie depth) with an attacker-controlled input size.

### Impact Explanation
An unauthenticated public-RPC caller can consume disproportionate CPU (repeated trie hashing/traversal for each key) and memory (accumulating proof node lists and forming a large JSON payload) with a single request. Because JSON-RPC calls are not gas-metered like transactions, this is a cheap way to degrade a node's RPC responsiveness, and if issued repeatedly/concurrently, can exhaust node resources (CPU, memory, DB I/O) — a resource-exhaustion Denial of Service against the RPC-serving node(s), consistent with CWE-231/CVSS High DoS characteristics of the reported advisory.

### Likelihood Explanation
High. `eth_getProof` and `kaia_getProof` are standard, publicly exposed read-only RPC methods with no special authorization. Constructing a request with a very large `storageKeys` array requires no special privilege, funds, or precondition beyond having RPC access to a public endpoint — which is explicitly listed as a reachable actor for this class of finding.

### Recommendation
Enforce an upper bound on the number of storage keys accepted per `eth_getProof`/`kaia_getProof` call (e.g., a small constant such as a few hundred, consistent with what many Ethereum-compatible clients enforce), returning an error when exceeded, before allocating slices or invoking `storageTrie.Prove`. Consider also bounding total response size and/or applying per-call CPU/time budgeting for proof generation. Note: I could not find any existing per-request length cap for this parameter in the codebase, nor a generic RPC-level array-size/response-size limit that would mitigate this specific case; if such general RPC protections exist outside the files I inspected (e.g., configurable `--rpc.*` flags), they should be verified and, if absent, added.

### Proof of Concept
1. Start a Kaia node exposing the JSON-RPC HTTP endpoint (`8551` by default).
2. Send:
```
curl -X POST http://localhost:8551 \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0","id":1,"method":"eth_getProof",
    "params":[
      "0x0000000000000000000000000000000000000400",
      [ /* generate e.g. 100000 distinct 0x...-prefixed 32-byte hex strings here */ ],
      "latest"
    ]
  }'
```
3. Observe that `doGetProof` (`api/api_eth.go:296`) iterates the entire supplied key list, invoking `storageTrie.Prove` for every one (`storage/statedb/proof.go:50`), with no upper bound rejecting the oversized request — causing elevated CPU/memory usage and a large response proportional to the attacker-chosen array size.

### Citations

**File:** api/api_eth.go (L296-301)
```go
func doGetProof(ctx context.Context, b Backend, address common.Address, storageKeys []string, blockNrOrHash rpc.BlockNumberOrHash) (*EthAccountResult, error) {
	var (
		keys         = make([]common.Hash, len(storageKeys))
		keyLengths   = make([]int, len(storageKeys))
		storageProof = make([]EthStorageResult, len(storageKeys))
	)
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

**File:** api/api_kaia_blockchain.go (L637-639)
```go
func (s *KaiaBlockChainAPI) GetProof(ctx context.Context, address common.Address, storageKeys []string, blockNrOrHash rpc.BlockNumberOrHash) (*EthAccountResult, error) {
	return doGetProof(ctx, s.b, address, storageKeys, blockNrOrHash)
}
```

**File:** storage/statedb/proof.go (L50-89)
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
```
