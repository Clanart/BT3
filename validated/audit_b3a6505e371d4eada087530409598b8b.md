### Title
Unbounded, ungated iteration over CosmWasm contract state via the legacy `custom/wasm/contract-state-all` ABCI query path enables RPC-node memory-exhaustion DoS - ([File: sei-wasmd/x/wasm/keeper/legacy_querier.go])

### Summary
The Ceph CVE describes an unprivileged/authenticated user growing an OMAP index (bucket index) large enough that a normal read path that iterates the whole index without bounds exhausts server resources. The `sei-wasmd` legacy querier exposes an analogous pattern: `QueryMethodContractStateAll` performs a full, unbounded iteration of a contract's entire KV store and materializes every key/value into memory before returning it, with no pagination, no gas metering, and no scan-limit enforcement, unlike its gRPC sibling.

### Finding Description
`queryContractState` in the legacy wasm querier handles the `contract-state-all` sub-query by calling `keeper.IterateContractState` and appending every entry to an in-memory slice, then JSON-marshaling the whole thing: [1](#0-0) 

`IterateContractState` itself walks the full prefix range for the contract with no `Iterator(start, end)` bound and no callback-driven cutoff other than what the caller chooses (here, none is used): [2](#0-1) 

Compare this to the gRPC `AllContractState` handler, which is properly paginated via `query.FilteredPaginateForContext`, bounding the amount of data scanned/returned per call: [3](#0-2) 

Also note that unlike `QueryMethodContractStateSmart`, which wraps execution with a gas meter (`sdk.NewGasMeterWithMultiplier`), the `QueryMethodContractStateAll` and `QueryMethodContractStateRaw` branches have no gas metering at all: [4](#0-3) 

This legacy querier is wired into the module manager's `LegacyQuerierHandler`/`QuerierRoute` and dispatched through the SDK's generic module query-router registration: [5](#0-4) [6](#0-5) 

The wider codebase shows this class of vulnerability is well understood and actively mitigated elsewhere (pagination scan budgets in `sei-cosmos/types/query/scan_limit.go`, `filtered_pagination.go`, and store-tracer memory caps in `sei-cosmos/types/tracer.go`), which strengthens the case that the legacy `contract-state-all` path was simply missed rather than intentionally left unbounded. [7](#0-6) 

### Impact Explanation
A CosmWasm contract can accumulate an arbitrarily large number of state entries through ordinary, permissionless `Execute` calls (e.g., a contract that stores one KV entry per call). Any public-RPC client can then submit a single `abci_query` request with path `custom/wasm/contract-state-all/<addr>` against that contract. The handler performs a full unbounded store scan, builds the full result set in memory, and JSON-marshals it in one shot, with no gas charge and no size/iteration cap. Against a sufficiently large contract state this can exhaust memory/CPU on a default-configuration public RPC node, producing a query-path denial of service — directly analogous to the Ceph RGW OMAP DoS in CVE-2018-16846, where an authenticated user grows a bucket index large enough that an unbounded read triggers resource exhaustion.

### Likelihood Explanation
Reaching this requires only two unprivileged actions: (1) deploying/using a CW contract and issuing enough `Execute` messages to grow its state (fully permissionless), and (2) issuing a single `abci_query` (or the equivalent `wasmd query wasm contract-state all` CLI call, which is just a thin wrapper over the same ABCI query) against that contract from any public RPC endpoint. No governance, validator, or operator privilege is needed, and the query path bypasses the gas metering and pagination protections that the equivalent gRPC endpoint enforces.

### Recommendation
- Apply the same pagination/scan-limit discipline used by the gRPC `AllContractState` handler (`query.FilteredPaginateForContext`) to the legacy querier's `QueryMethodContractStateAll` path, or remove/disable the unpaginated legacy handler in favor of the paginated gRPC query.
- Add gas metering (as already done for `QueryMethodContractStateSmart`) to the `contract-state-all` and `contract-state-raw` legacy query branches so cost scales with work performed.
- Enforce a maximum iteration/result-size budget (mirroring `sei-cosmos/types/query/scan_limit.go`'s `MaxScanLimit`/`iterationBudget`) inside `IterateContractState` when invoked from untrusted query contexts.

### Proof of Concept
1. Deploy a CosmWasm contract whose `Execute` handler writes a new unique key to its own storage per call (no special permissions required).
2. Submit enough `Execute` transactions (batched/looped) to grow the contract's KV store to a large number of entries (e.g., hundreds of thousands to millions of small keys), which is only bounded by ordinary per-tx gas/fee cost and is achievable over time by a single unprivileged account.
3. From any public RPC node, issue an ABCI query equivalent to `wasmd query wasm contract-state all <contract_addr>` (i.e., `abci_query` with path `custom/wasm/contract-state-all/<contract_addr>`).
4. Observe the node's query-serving goroutine perform a full unbounded iteration via `IterateContractState` and buffer the entire dataset in memory for JSON marshaling, with no gas charge or pagination cutoff, consuming memory/CPU proportional to total contract state size and potentially causing the node to run out of memory or become unresponsive to other RPC requests.

### Citations

**File:** sei-wasmd/x/wasm/keeper/legacy_querier.go (L92-121)
```go
	switch queryMethod {
	case QueryMethodContractStateAll:
		resultData := make([]types.Model, 0)
		// this returns a serialized json object (which internally encoded binary fields properly)
		keeper.IterateContractState(ctx, contractAddr, func(key, value []byte) bool {
			resultData = append(resultData, types.Model{Key: key, Value: value})
			return false
		})
		bz, err := json.Marshal(resultData)
		if err != nil {
			return nil, sdkerrors.Wrap(sdkerrors.ErrJSONMarshal, err.Error())
		}
		return bz, nil
	case QueryMethodContractStateRaw:
		// this returns the raw data from the state, base64-encoded
		return keeper.QueryRaw(ctx, contractAddr, data), nil
	case QueryMethodContractStateSmart:
		// we enforce a subjective gas limit on all queries to avoid infinite loops
		ctx = ctx.WithGasMeter(sdk.NewGasMeterWithMultiplier(ctx, gasLimit))
		msg := types.RawContractMessage(data)
		if err := msg.ValidateBasic(); err != nil {
			return nil, sdkerrors.Wrap(err, "json msg")
		}
		// this returns raw bytes (must be base64-encoded)
		bz, err := keeper.QuerySmart(ctx, contractAddr, msg)
		return bz, err
	default:
		return nil, sdkerrors.Wrap(sdkerrors.ErrUnknownRequest, queryMethod)
	}
}
```

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L814-827)
```go
// IterateContractState iterates through all elements of the key value store for the given contract address and passes
// them to the provided callback function. The callback method can return true to abort early.
func (k Keeper) IterateContractState(ctx sdk.Context, contractAddress sdk.AccAddress, cb func(key, value []byte) bool) {
	prefixStoreKey := types.GetContractStorePrefix(contractAddress)
	prefixStore := prefix.NewStore(ctx.KVStore(k.storeKey), prefixStoreKey)
	iter := prefixStore.Iterator(nil, nil)
	defer func() { _ = iter.Close() }()

	for ; iter.Valid(); iter.Next() {
		if cb(iter.Key(), iter.Value()) {
			break
		}
	}
}
```

**File:** sei-wasmd/x/wasm/keeper/querier.go (L114-145)
```go
func (q grpcQuerier) AllContractState(c context.Context, req *types.QueryAllContractStateRequest) (*types.QueryAllContractStateResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "empty request")
	}
	contractAddr, err := sdk.AccAddressFromBech32(req.Address)
	if err != nil {
		return nil, err
	}
	ctx := sdk.UnwrapSDKContext(c)
	if !q.keeper.HasContractInfo(ctx, contractAddr) {
		return nil, types.ErrNotFound
	}

	r := make([]types.Model, 0)
	prefixStore := prefix.NewStore(ctx.KVStore(q.storeKey), types.GetContractStorePrefix(contractAddr))
	pageRes, err := query.FilteredPaginateForContext(ctx, prefixStore, req.Pagination, func(key []byte, value []byte, accumulate bool) (bool, error) {
		if accumulate {
			r = append(r, types.Model{
				Key:   key,
				Value: value,
			})
		}
		return true, nil
	})
	if err != nil {
		return nil, err
	}
	return &types.QueryAllContractStateResponse{
		Models:     r,
		Pagination: pageRes,
	}, nil
}
```

**File:** sei-wasmd/x/wasm/module.go (L165-180)
```go
func (am AppModule) LegacyQuerierHandler(amino *codec.LegacyAmino) sdk.Querier { //nolint:staticcheck
	return keeper.NewLegacyQuerier(am.keeper, am.keeper.QueryGasLimit())
}

// RegisterInvariants registers the wasm module invariants.
func (am AppModule) RegisterInvariants(ir sdk.InvariantRegistry) {}

// Route returns the message routing key for the wasm module.
func (am AppModule) Route() sdk.Route {
	return sdk.NewRoute(RouterKey, NewHandler(keeper.NewDefaultPermissionKeeper(am.keeper)))
}

// QuerierRoute returns the wasm module's querier route name.
func (AppModule) QuerierRoute() string {
	return QuerierRoute
}
```

**File:** sei-cosmos/types/module/module.go (L344-354)
```go
// RegisterRoutes registers all module routes and module querier routes
func (m *Manager) RegisterRoutes(router sdk.Router, queryRouter sdk.QueryRouter, legacyQuerierCdc *codec.LegacyAmino) {
	for _, module := range m.Modules {
		if r := module.Route(); !r.Empty() {
			router.AddRoute(r)
		}
		if r := module.QuerierRoute(); r != "" {
			queryRouter.AddRoute(r, module.LegacyQuerierHandler(legacyQuerierCdc))
		}
	}
}
```

**File:** sei-cosmos/types/query/scan_limit.go (L50-75)
```go
type iterationBudget struct {
	params    scanLimitParams
	count     uint64
	truncated bool
}

func newIterationBudget(params scanLimitParams) *iterationBudget {
	if !params.enforce || params.maxIterations == 0 {
		return nil
	}
	return &iterationBudget{params: params}
}

// begin marks the start of a store iteration. When the flat iteration budget is
// exhausted it returns the resume key and stop=true without consuming the entry.
func (b *iterationBudget) begin(key []byte) (resumeKey []byte, stop bool) {
	if b == nil {
		return nil, false
	}
	if b.count >= b.params.maxIterations {
		b.truncated = true
		return key, true
	}
	b.count++
	return nil, false
}
```
