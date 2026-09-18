### Title
Unbounded iteration in `TokenFactory` `DenomsFromCreator` wasm query bypasses pagination and can OOG/DoS querying nodes and contracts - (File: `x/tokenfactory/client/wasm/query.go`, `x/tokenfactory/keeper/creators.go`)

### Summary
The CosmWasm custom-query path for `tokenfactory`'s `DenomsFromCreator` ignores the pagination that the gRPC query enforces and instead calls an explicitly unbounded, unpaginated iterator over every denom a creator has ever created. Because any account can create tokenfactory denoms (bounded only by the denom-creation fee, not by a hard cap), a creator address can accumulate an arbitrarily large denom list, and any CosmWasm contract or public smart-query client that asks for that creator's denoms will force a full, unbounded KV-store scan.

### Finding Description
The gRPC `DenomsFromCreator` query correctly paginates: [1](#0-0) 
which calls the paginated helper: [2](#0-1) 

However, the CosmWasm binding for the exact same query does **not** use pagination at all — it calls `GetAllDenomsFromCreator`, which iterates the creator's entire prefix store with no limit and is explicitly documented as unbounded: [3](#0-2) [4](#0-3) 

This handler is wired into the standard wasmd custom querier, reachable from any CosmWasm contract's `WasmQuery::Custom` call (and, transitively, from any public "smart query" RPC/CLI call against a contract that forwards user input into this query, or a purpose-built contract designed to reflect it): [5](#0-4) [6](#0-5) 

The comment in `creators.go` — "Safe to use in the wasm query path: gas metering bounds execution cost, so unbounded iteration does not pose a DoS risk" — assumes the only consumer is gas-metered contract execution. But the query router is also reachable through the wasm "smart query" gRPC/REST/CLI endpoints, which are typically bounded by a configurable node-level query gas limit (often set very high or effectively unbounded in default node configuration), not by the caller's own transaction gas. Any tokenfactory denom creator (an unprivileged transaction sender — creation is rate-limited only by a fee, not a hard cap) can therefore grow their own creator-prefix store to an arbitrary size, and any subsequent smart-query/contract call requesting `DenomsFromCreator` for that address triggers a full, unbounded iteration.

### Impact Explanation
An attacker (any tokenfactory denom creator) can create a very large number of denoms under one creator address over time, then have any contract/off-chain integrator query `DenomsFromCreator` for that address through the CosmWasm custom-query path. Because this path skips pagination, the query degrades to O(n) unbounded store iteration per call. Depending on node query-gas configuration this can consume excessive CPU/IO on a full/RPC node servicing smart queries, denying that query to legitimate users/integrators and degrading node responsiveness — directly analogous to the referenced Y2K-Finance issue where unpaginated iteration over an attacker-growable collection breaks external protocol integrations and frontends that depend on the view function.

### Likelihood Explanation
Likelihood is Medium: creating tokenfactory denoms requires paying the configured denom-creation fee for each denom, so the attack has a cost proportional to the number of denoms created, but there is no hard per-creator cap, so a moderately funded attacker can build an arbitrarily large list over time. No privileged access is required, and the vulnerable path is reachable by any CosmWasm contract that surfaces this custom query.

### Recommendation
Thread the caller-supplied pagination through to the CosmWasm binding instead of calling the unbounded `GetAllDenomsFromCreator`:
- Change `TokenFactoryWasmQueryHandler.GetDenomsFromCreator` (`x/tokenfactory/client/wasm/query.go`) to call the paginated `k.DenomsFromCreator`/`getDenomsFromCreator` path (the same one used by the gRPC query) and honor `req.Pagination`, capping the default/maximum page size if none is provided.
- Remove or correct the misleading "safe" comment on `GetAllDenomsFromCreator`, and restrict its use to contexts that are provably gas-metered per the initiating transaction (e.g., genesis export/migrations only), not to any externally reachable query path.

### Proof of Concept
1. Attacker account `A` repeatedly submits `MsgCreateDenom` (paying the applicable fee each time) to create N denoms (e.g., tens of thousands), all stored under `A`'s creator-prefix store via `addDenomFromCreator`.
2. A CosmWasm contract (or any contract exposing a pass-through query) issues `WasmQuery::Custom` with `SeiTokenFactoryQuery{DenomsFromCreator: {Creator: A}}`, routed through `CustomQuerier` → `HandleTokenFactoryQuery` → `TokenFactoryWasmQueryHandler.GetDenomsFromCreator`.
3. This calls `GetAllDenomsFromCreator(ctx, A)`, which iterates all N entries with no page limit, unlike the equivalent gRPC endpoint.
4. Repeated smart-queries against this contract for creator `A` force full-store scans on every servicing node, degrading or exhausting query-serving resources — reproducing the "unbounded loop in view function" DoS pattern from the referenced report.

### Citations

**File:** x/tokenfactory/keeper/grpc_query.go (L33-40)
```go
func (k Keeper) DenomsFromCreator(ctx context.Context, req *types.QueryDenomsFromCreatorRequest) (*types.QueryDenomsFromCreatorResponse, error) {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	denoms, pageRes, err := k.getDenomsFromCreator(sdkCtx, req.GetCreator(), req.GetPagination())
	if err != nil {
		return nil, err
	}
	return &types.QueryDenomsFromCreatorResponse{Denoms: denoms, Pagination: pageRes}, nil
}
```

**File:** x/tokenfactory/keeper/creators.go (L13-24)
```go
func (k Keeper) getDenomsFromCreator(ctx sdk.Context, creator string, pagination *query.PageRequest) ([]string, *query.PageResponse, error) {
	store := k.GetCreatorPrefixStore(ctx, creator)
	var denoms []string
	pageRes, err := query.Paginate(ctx, store, pagination, func(key []byte, _ []byte) error {
		denoms = append(denoms, string(key))
		return nil
	})
	if err != nil {
		return nil, nil, err
	}
	return denoms, pageRes, nil
}
```

**File:** x/tokenfactory/keeper/creators.go (L26-37)
```go
// GetAllDenomsFromCreator returns every denom for a creator with no page cap.
// Safe to use in the wasm query path: gas metering bounds execution cost, so unbounded iteration does not pose a DoS risk.
func (k Keeper) GetAllDenomsFromCreator(ctx sdk.Context, creator string) []string {
	store := k.GetCreatorPrefixStore(ctx, creator)
	iterator := store.Iterator(nil, nil)
	defer func() { _ = iterator.Close() }()
	var denoms []string
	for ; iterator.Valid(); iterator.Next() {
		denoms = append(denoms, string(iterator.Key()))
	}
	return denoms
}
```

**File:** x/tokenfactory/client/wasm/query.go (L24-27)
```go
func (handler TokenFactoryWasmQueryHandler) GetDenomsFromCreator(ctx sdk.Context, req *types.QueryDenomsFromCreatorRequest) (*types.QueryDenomsFromCreatorResponse, error) {
	denoms := handler.tokenfactoryKeeper.GetAllDenomsFromCreator(ctx, req.Creator)
	return &types.QueryDenomsFromCreatorResponse{Denoms: denoms}, nil
}
```

**File:** wasmbinding/queries.go (L99-130)
```go
func (qp QueryPlugin) HandleTokenFactoryQuery(ctx sdk.Context, queryData json.RawMessage) ([]byte, error) {
	var parsedQuery tokenfactorybindings.SeiTokenFactoryQuery
	if err := json.Unmarshal(queryData, &parsedQuery); err != nil {
		return nil, tokenfactorytypes.ErrParsingSeiTokenFactoryQuery
	}
	switch {
	case parsedQuery.DenomAuthorityMetadata != nil:
		res, err := qp.tokenfactoryHandler.GetDenomAuthorityMetadata(ctx, parsedQuery.DenomAuthorityMetadata)
		if err != nil {
			return nil, err
		}
		bz, err := json.Marshal(res)
		if err != nil {
			return nil, tokenfactorytypes.ErrEncodingDenomAuthorityMetadata
		}

		return bz, nil
	case parsedQuery.DenomsFromCreator != nil:
		res, err := qp.tokenfactoryHandler.GetDenomsFromCreator(ctx, parsedQuery.DenomsFromCreator)
		if err != nil {
			return nil, err
		}
		bz, err := json.Marshal(res)
		if err != nil {
			return nil, tokenfactorytypes.ErrEncodingDenomsFromCreator
		}

		return bz, nil
	default:
		return nil, tokenfactorytypes.ErrUnknownSeiTokenFactoryQuery
	}
}
```

**File:** x/tokenfactory/client/wasm/bindings/queries.go (L1-12)
```go
package bindings

import "github.com/sei-protocol/sei-chain/x/tokenfactory/types"

type SeiTokenFactoryQuery struct {
	// queries the tokenfactory authority metadata
	DenomAuthorityMetadata *types.QueryDenomAuthorityMetadataRequest `json:"denom_authority_metadata,omitempty"`
	// queries the tokenfactory denoms from a creator
	DenomsFromCreator *types.QueryDenomsFromCreatorRequest `json:"denoms_from_creator,omitempty"`
}


```
