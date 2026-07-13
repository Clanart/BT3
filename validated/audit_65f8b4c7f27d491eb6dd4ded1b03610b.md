### Title
Unbounded `FilterCriteria.Addresses` in JSON-RPC Filter Endpoints Enables RPC Server DoS - (File: `rpc/namespaces/ethereum/eth/filters/api.go`, `rpc/namespaces/ethereum/eth/filters/filters.go`, `rpc/websockets.go`)

---

### Summary

Ethermint's JSON-RPC filter endpoints (`eth_getLogs`, `eth_newFilter`, `eth_getFilterLogs`, `eth_getFilterChanges`, and `eth_subscribe` with `logs`) accept a `FilterCriteria.Addresses` slice with no upper-bound check. An unprivileged attacker can supply hundreds of thousands of addresses in a single request, triggering O(n) keccak hashing during filter construction and O(n) linear scans on every block and every log entry. Because Ethermint's JSON-RPC server runs in the same process as the CometBFT/Cosmos validator node, sustained CPU exhaustion from this path can starve the consensus engine of resources, causing missed blocks and potential consensus failure.

---

### Finding Description

**No address limit exists anywhere in the codebase.** A grep for `maxAddresses`, `maxTopics`, `MaxAddresses`, or `MaxTopics` returns zero results. There is no analogous guard to the `maxTopics` constant found in comparable codebases.

**Root cause — `GetLogs` (HTTP path):**

`PublicFilterAPI.GetLogs` accepts the caller-supplied `crit.Addresses` slice and passes it directly to `NewRangeFilter` or `NewBlockFilter` without any length check: [1](#0-0) 

**Root cause — `NewRangeFilter` (keccak per address):**

`NewRangeFilter` allocates a `[][]byte` of length `len(addresses)` and then calls `createBloomFilters`, which calls `calcBloomIVs` (a keccak hash) for every single address: [2](#0-1) [3](#0-2) 

With 200,000 addresses this is 200,000 keccak hashes at filter construction time alone.

**Root cause — `bloomFilter` (O(n) per block):**

For every block scanned, `blockLogs` calls `bloomFilter`, which iterates over every address doing a `BloomLookup`: [4](#0-3) 

**Root cause — `FilterLogs`/`includes` (O(n) per log):**

For every log that passes the bloom pre-filter, `FilterLogs` calls `includes`, which is a linear scan over all addresses: [5](#0-4) 

**Root cause — `NewFilter` + `GetFilterChanges` (persistent polling):**

`NewFilter` stores the unbounded `criteria` without any address count check: [6](#0-5) 

Every subsequent `GetFilterChanges` call re-runs `FilterLogs` with the full address list against all accumulated logs: [7](#0-6) 

**Root cause — `subscribeLogs` WebSocket (persistent per-block DoS):**

The WebSocket `eth_subscribe logs` path in `subscribeLogs` appends addresses without any limit check: [8](#0-7) 

Then on every new block, `rpcfilters.FilterLogs` is called with the full unbounded address list: [9](#0-8) 

This is the most dangerous path: a single persistent WebSocket subscription with 200,000 addresses causes O(n) work on every block indefinitely, and multiple concurrent subscriptions multiply the effect.

---

### Impact Explanation

Ethermint's JSON-RPC server runs in the same OS process as the CometBFT validator node. Sustained CPU exhaustion from repeated `eth_getLogs` requests or persistent WebSocket subscriptions with large address lists can starve the consensus engine goroutines of CPU time, causing the validator to miss `ProposeTimeout`/`PrevoteTimeout` deadlines, fall behind in block processing, and ultimately be slashed or cause chain liveness failure. This matches the allowed High impact: "Public JSON-RPC path exposes a reachable route to chain halt / consensus failure."

The `subscribeLogs` WebSocket vector is particularly severe because a single connection causes ongoing per-block work with no timeout or cleanup until the connection is closed.

---

### Likelihood Explanation

- Requires zero authentication; any public JSON-RPC endpoint is reachable by any internet client.
- A single HTTP request with ~200,000 addresses fits within the default ~10 MB body limit (≈ 45 bytes × 200,000 = 9 MB).
- The WebSocket path requires only a single `eth_subscribe` call.
- No special knowledge of the chain state is needed; addresses can be arbitrary.
- The attack is trivially scriptable and can be repeated or parallelized with a small botnet.

---

### Recommendation

1. Add a `maxAddresses` constant (e.g., `maxAddresses = 100`) and enforce it at the top of `GetLogs`, `NewFilter`, `GetFilterLogs`, and `subscribeLogs`, analogous to how `Topics` is bounded in comparable implementations.
2. In `subscribeLogs`, validate `len(crit.Addresses)` before storing the subscription.
3. Consider replacing the O(n) `includes` linear scan with a `map[common.Address]struct{}` lookup for large address sets.

---

### Proof of Concept

**HTTP path (`eth_getLogs`):**

```
POST / HTTP/1.1
Content-Type: application/json

{
  "jsonrpc":"2.0","method":"eth_getLogs","id":1,
  "params":[{"fromBlock":"0x1","toBlock":"0x2",
    "address":["0x0000...0001","0x0000...0002", ... /* 200,000 entries */]}]
}
```

Each request triggers:
- 200,000 keccak hashes in `createBloomFilters`
- 200,000 `BloomLookup` calls per block scanned in `bloomFilter`
- 200,000 address comparisons per log in `includes`

**WebSocket path (`eth_subscribe`):**

```json
{"jsonrpc":"2.0","method":"eth_subscribe","id":1,
 "params":["logs",{"address":["0x...","0x...", /* 200,000 entries */]}]}
```

This registers a persistent subscription. On every new block, `FilterLogs` iterates over all 200,000 addresses. Opening N concurrent such subscriptions multiplies the per-block CPU cost by N, with no server-side limit on concurrent subscriptions beyond the general connection cap. [10](#0-9) [11](#0-10) [12](#0-11) [13](#0-12) [14](#0-13) [9](#0-8)

### Citations

**File:** rpc/namespaces/ethereum/eth/filters/api.go (L191-215)
```go
func (api *PublicFilterAPI) NewFilter(criteria filters.FilterCriteria) (rpc.ID, error) {
	api.filtersMu.Lock()
	defer api.filtersMu.Unlock()

	if len(api.filters) >= int(api.backend.RPCFilterCap()) {
		return rpc.ID(""), fmt.Errorf("error creating filter: max limit reached")
	}

	if criteria.FromBlock != nil && criteria.ToBlock != nil &&
		criteria.FromBlock.Int64() >= 0 && criteria.ToBlock.Int64() >= 0 &&
		criteria.FromBlock.Int64() > criteria.ToBlock.Int64() {
		return rpc.ID(""), &types.InvalidParamsError{Message: "invalid block range params"}
	}

	id := rpc.NewID()
	_, offset := api.events.LogStream().ReadNonBlocking(-1)
	api.filters[id] = &filter{
		typ:      filters.LogsSubscription,
		deadline: time.NewTimer(deadline),
		crit:     criteria,
		offset:   offset,
	}

	return id, nil
}
```

**File:** rpc/namespaces/ethereum/eth/filters/api.go (L220-246)
```go
func (api *PublicFilterAPI) GetLogs(ctx context.Context, crit filters.FilterCriteria) ([]*ethtypes.Log, error) {
	var filter *Filter
	if crit.BlockHash != nil {
		// Block filter requested, construct a single-shot filter
		filter = NewBlockFilter(api.logger, api.backend, crit)
	} else {
		// Convert the RPC block numbers into internal representations
		begin := rpc.LatestBlockNumber.Int64()
		if crit.FromBlock != nil {
			begin = crit.FromBlock.Int64()
		}
		end := rpc.LatestBlockNumber.Int64()
		if crit.ToBlock != nil {
			end = crit.ToBlock.Int64()
		}
		// Construct the range filter
		filter = NewRangeFilter(api.logger, api.backend, begin, end, crit.Addresses, crit.Topics)
	}

	// Run the filter and return all the logs
	logs, err := filter.Logs(ctx, int(api.backend.RPCLogsCap()), int64(api.backend.RPCBlockRangeCap()))
	if err != nil {
		return nil, err
	}

	return returnLogs(logs), err
}
```

**File:** rpc/namespaces/ethereum/eth/filters/api.go (L357-370)
```go
	case filters.LogsSubscription:
		var (
			logs  []*ethtypes.Log
			chunk []*ethtypes.Log
		)
		for {
			chunk, f.offset = api.events.LogStream().ReadNonBlocking(f.offset)
			if len(chunk) == 0 {
				break
			}
			chunk = FilterLogs(chunk, f.crit.FromBlock, f.crit.ToBlock, f.crit.Addresses, f.crit.Topics)
			logs = append(logs, chunk...)
		}
		return returnLogs(logs), nil
```

**File:** rpc/namespaces/ethereum/eth/filters/filters.go (L62-92)
```go
func NewRangeFilter(logger log.Logger, backend Backend, begin, end int64, addresses []common.Address, topics [][]common.Hash) *Filter {
	// Flatten the address and topic filter clauses into a single bloombits filter
	// system. Since the bloombits are not positional, nil topics are permitted,
	// which get flattened into a nil byte slice.
	filtersBz := make([][][]byte, 0, 1+len(topics))
	if len(addresses) > 0 {
		filter := make([][]byte, len(addresses))
		for i, address := range addresses {
			filter[i] = address.Bytes()
		}
		filtersBz = append(filtersBz, filter)
	}

	for _, topicList := range topics {
		filter := make([][]byte, len(topicList))
		for i, topic := range topicList {
			filter[i] = topic.Bytes()
		}
		filtersBz = append(filtersBz, filter)
	}

	// Create a generic filter and convert it into a range filter
	criteria := filters.FilterCriteria{
		FromBlock: big.NewInt(begin),
		ToBlock:   big.NewInt(end),
		Addresses: addresses,
		Topics:    topics,
	}

	return newFilter(logger, backend, criteria, createBloomFilters(filtersBz, logger))
}
```

**File:** rpc/namespaces/ethereum/eth/filters/filters.go (L236-268)
```go
func createBloomFilters(filters [][][]byte, logger log.Logger) [][]BloomIV {
	bloomFilters := make([][]BloomIV, 0)
	for _, filter := range filters {
		// Gather the bit indexes of the filter rule, special casing the nil filter
		if len(filter) == 0 {
			continue
		}
		bloomIVs := make([]BloomIV, len(filter))

		// Transform the filter rules (the addresses and topics) to the bloom index and value arrays
		// So it can be used to compare with the bloom of the block header. If the rule has any nil
		// clauses. The rule will be ignored.
		for i, clause := range filter {
			if clause == nil {
				bloomIVs = nil
				break
			}

			iv, err := calcBloomIVs(clause)
			if err != nil {
				bloomIVs = nil
				logger.Error("calcBloomIVs error", "error", err)
				break
			}

			bloomIVs[i] = iv
		}
		// Accumulate the filter rules if no nil rule was within
		if bloomIVs != nil {
			bloomFilters = append(bloomFilters, bloomIVs)
		}
	}
	return bloomFilters
```

**File:** rpc/namespaces/ethereum/eth/filters/utils.go (L31-73)
```go
func FilterLogs(logs []*ethtypes.Log, fromBlock, toBlock *big.Int, addresses []common.Address, topics [][]common.Hash) []*ethtypes.Log {
	var ret []*ethtypes.Log
Logs:
	for _, log := range logs {
		if fromBlock != nil && fromBlock.Int64() >= 0 && fromBlock.Uint64() > log.BlockNumber {
			continue
		}
		if toBlock != nil && toBlock.Int64() >= 0 && toBlock.Uint64() < log.BlockNumber {
			continue
		}
		if len(addresses) > 0 && !includes(addresses, log.Address) {
			continue
		}
		// If the to filtered topics is greater than the amount of topics in logs, skip.
		if len(topics) > len(log.Topics) {
			continue
		}
		for i, sub := range topics {
			match := len(sub) == 0 // empty rule set == wildcard
			for _, topic := range sub {
				if log.Topics[i] == topic {
					match = true
					break
				}
			}
			if !match {
				continue Logs
			}
		}
		ret = append(ret, log)
	}
	return ret
}

func includes(addresses []common.Address, a common.Address) bool {
	for _, addr := range addresses {
		if addr == a {
			return true
		}
	}

	return false
}
```

**File:** rpc/namespaces/ethereum/eth/filters/utils.go (L76-103)
```go
func bloomFilter(bloom ethtypes.Bloom, addresses []common.Address, topics [][]common.Hash) bool {
	if len(addresses) > 0 {
		var included bool
		for _, addr := range addresses {
			if ethtypes.BloomLookup(bloom, addr) {
				included = true
				break
			}
		}
		if !included {
			return false
		}
	}

	for _, sub := range topics {
		included := len(sub) == 0 // empty rule set == wildcard
		for _, topic := range sub {
			if ethtypes.BloomLookup(bloom, topic) {
				included = true
				break
			}
		}
		if !included {
			return false
		}
	}
	return true
}
```

**File:** rpc/websockets.go (L678-694)
```go
		if params["address"] != nil {
			switch address := params["address"].(type) {
			case string:
				crit.Addresses = []common.Address{common.HexToAddress(address)}
			case []interface{}:
				for _, addr := range address {
					address, ok := addr.(string)
					if !ok {
						return nil, errors.New("invalid address")
					}

					crit.Addresses = append(crit.Addresses, common.HexToAddress(address))
				}
			default:
				return nil, errors.New("invalid addresses; must be address or array of addresses")
			}
		}
```

**File:** rpc/websockets.go (L759-761)
```go
	go api.events.LogStream().Subscribe(ctx, func(txLogs []*ethtypes.Log, _ int) error {
		logs := rpcfilters.FilterLogs(txLogs, crit.FromBlock, crit.ToBlock, crit.Addresses, crit.Topics)
		if len(logs) == 0 {
```
