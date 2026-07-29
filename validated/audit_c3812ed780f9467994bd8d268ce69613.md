## Finding: Missing precompile-map isolation in `StateOverride.Apply` corrupts the shared/global precompile table

### Title
Unauthenticated `eth_call`/`eth_estimateGas` StateOverride permanently deletes precompiles from the shared global precompile table, causing non-deterministic EVM execution and AppHash divergence - (File: `x/vm/keeper/state_transition.go`)

### Summary
`ApplyMessageWithConfig` calls `vm.ActivePrecompiledContracts(rules)` and passes the returned map directly into `StateOverride.Apply` without cloning it first, unlike the accompanying unit test which correctly does `maps.Clone(precompiles)` before calling `Apply`. [1](#0-0) [2](#0-1) 

### Finding Description
`vm.ActivePrecompiledContracts` (upstream go-ethereum `core/vm/contracts.go`) returns a reference to a package-level global map (e.g. `PrecompiledContractsBerlin`/`PrecompiledContractsPrague`), not a copy. `StateOverride.Apply` unconditionally deletes any overridden address from that map whenever it matches a known precompile — regardless of whether `MovePrecompileTo` is set:

```go
p, isPrecompile := precompiles[addr]
...
if isPrecompile {
    delete(precompiles, addr)
}
``` [3](#0-2) 

Since the production call site does not clone the map before calling `Apply`:
```go
precompiles := vm.ActivePrecompiledContracts(rules)
if err := overrides.Apply(stateDB, precompiles); err != nil {
    return nil, errorsmod.Wrap(err, "failed to apply state override")
}
evm.WithPrecompiles(precompiles)
``` [4](#0-3) 

any attacker able to invoke `eth_call`, `eth_estimateGas`, or a call path that reaches `EthCall`/`ApplyMessageWithConfig` with a `StateOverride` keyed on a well-known precompile address (e.g. `0x1` ecrecover) will trigger `delete(precompiles, addr)` on the actual shared package-level map used by the go-ethereum VM package for that fork rule set. This mutation is process-global and persists beyond the single query/goroutine — it is not scoped to the per-query `sdk.Context` isolation that normally protects query-only state changes.

This is reachable entirely unprivileged through the public JSON-RPC surface: `PublicAPI.Call` → `Backend.DoCall` → gRPC `EthCall` → `Keeper.EthCall` → `ApplyMessageWithConfig`. [5](#0-4) [6](#0-5) [7](#0-6) 

### Impact Explanation
After such a call, the affected node's in-process precompile table permanently lacks the deleted precompile for the remainder of the process lifetime (until restart), for every subsequent EVM execution on that node — including real, consensus-relevant transactions in `ApplyTransaction`, which reuses the same `ApplyMessageWithConfig` and the same underlying go-ethereum global maps. A transaction that calls `ecrecover` (address `0x1`) on the corrupted node will execute differently (treating `0x1` as a non-precompiled account, e.g. returning empty output) than on unaffected nodes, or than it would have prior to the malicious `eth_call`. Because block execution must be deterministic across all validators for the same input, this divergence causes an AppHash mismatch / consensus fork between the corrupted node and honest nodes, satisfying the "critical chain halt / consensus fork via non-determinism triggered by an unprivileged user" impact category.

### Likelihood Explanation
High likelihood: `eth_call` with a `StateOverride` (`overrides` parameter) is a completely standard, unauthenticated JSON-RPC feature exposed to any client, requiring no special privileges, tokens, or transaction fees to invoke (aside from RPC gas caps). Triggering the bug requires nothing more than a single override on address `0x1`–`0x9` with any field set (e.g. balance), which the query handler unmarshals directly from the request into `StateOverride` without cloning the precompile map anywhere in the call chain.

### Recommendation
Clone the precompile map returned by `vm.ActivePrecompiledContracts(rules)` before passing it to `StateOverride.Apply`, matching the pattern already used in `rpc/types/types_test.go`:
```go
precompiles := maps.Clone(vm.ActivePrecompiledContracts(rules))
```
This ensures per-call/per-query overrides never mutate the shared global precompile table used by real transaction execution.

### Proof of Concept
1. Send `eth_call` (or the gRPC `EthCall`/`EstimateGas` query) with:
```json
{
  "to": "0x0000000000000000000000000000000000000000",
  "data": "0x"
}
```
and `stateOverride`:
```json
{
  "0x0000000000000000000000000000000000000001": { "balance": "0x1" }
}
```
2. `Keeper.EthCall` unmarshals the override and calls `ApplyMessageWithConfig`, which fetches `precompiles := vm.ActivePrecompiledContracts(rules)` (a reference to the global map) and calls `overrides.Apply(stateDB, precompiles)`, which executes `delete(precompiles, 0x1)` on the shared map because `isPrecompile` is true for `0x1`. [3](#0-2) 
3. Subsequently, submit a real transaction whose contract logic calls `ecrecover` (precompile `0x1`). On the node that processed the malicious `eth_call`, the call to `0x1` no longer executes as a precompile (since it was removed from the shared map), producing a different result/gas usage than an unaffected control node that never received the override, causing state root divergence.

### Citations

**File:** x/vm/keeper/state_transition.go (L405-412)
```go
	rules := ethCfg.Rules(evm.Context.BlockNumber, true, evm.Context.Time)
	if overrides != nil {
		precompiles := vm.ActivePrecompiledContracts(rules)
		if err := overrides.Apply(stateDB, precompiles); err != nil {
			return nil, errorsmod.Wrap(err, "failed to apply state override")
		}
		evm.WithPrecompiles(precompiles)
	}
```

**File:** rpc/types/types_test.go (L88-91)
```go
	for name, tc := range testCases {
		t.Run(name, func(t *testing.T) {
			cpy := maps.Clone(precompiles)
			err := tc.overrides.Apply(db, cpy)
```

**File:** rpc/types/types.go (L101-120)
```go
		p, isPrecompile := precompiles[addr]
		// The MoveTo feature makes it possible to move a precompile
		// code to another address. If the target address is another precompile
		// the code for the latter is lost for this session.
		// Note the destination account is not cleared upon move.
		if account.MovePrecompileTo != nil {
			if !isPrecompile {
				return fmt.Errorf("account %s is not a precompile", addr.Hex())
			}
			// Refuse to move a precompile to an address that has been
			// or will be overridden.
			if diff.has(*account.MovePrecompileTo) {
				return fmt.Errorf("account %s is already overridden", account.MovePrecompileTo.Hex())
			}
			precompiles[*account.MovePrecompileTo] = p
			dirtyAddrs[*account.MovePrecompileTo] = struct{}{}
		}
		if isPrecompile {
			delete(precompiles, addr)
		}
```

**File:** rpc/namespaces/ethereum/eth/api.go (L284-300)
```go
func (e *PublicAPI) Call(
	args evmtypes.TransactionArgs,
	blockNrOrHash rpctypes.BlockNumberOrHash,
	overrides *json.RawMessage,
) (hexutil.Bytes, error) {
	e.logger.Debug("eth_call", "args", args, "block number or hash", blockNrOrHash)

	blockNum, err := e.backend.BlockNumberFromComet(blockNrOrHash)
	if err != nil {
		return nil, err
	}
	data, err := e.backend.DoCall(args, blockNum, overrides)
	if err != nil {
		return []byte{}, err
	}

	return (hexutil.Bytes)(data.Ret), nil
```

**File:** rpc/backend/call_tx.go (L360-417)
```go
func (b *Backend) DoCall(
	args evmtypes.TransactionArgs,
	blockNr rpctypes.BlockNumber,
	overrides *json.RawMessage,
) (*evmtypes.MsgEthereumTxResponse, error) {
	bz, err := json.Marshal(&args)
	if err != nil {
		return nil, err
	}
	header, err := b.CometHeaderByNumber(blockNr)
	if err != nil {
		// the error message imitates geth behavior
		return nil, errors.New("header not found")
	}

	var bzOverrides []byte
	if overrides != nil {
		bzOverrides = *overrides
	}

	req := evmtypes.EthCallRequest{
		Args:            bz,
		GasCap:          b.RPCGasCap(),
		ProposerAddress: sdk.ConsAddress(header.Header.ProposerAddress),
		ChainId:         b.EvmChainID.Int64(),
		Overrides:       bzOverrides,
	}

	// From ContextWithHeight: if the provided height is 0,
	// it will return an empty context and the gRPC query will use
	// the latest block height for querying.
	ctx := rpctypes.ContextWithHeight(blockNr.Int64())
	timeout := b.RPCEVMTimeout()

	// Setup context so it may be canceled the call has completed
	// or, in case of unmetered gas, setup a context with a timeout.
	var cancel context.CancelFunc
	if timeout > 0 {
		ctx, cancel = context.WithTimeout(ctx, timeout)
	} else {
		ctx, cancel = context.WithCancel(ctx)
	}

	// Make sure the context is canceled when the call has completed
	// this makes sure resources are cleaned up.
	defer cancel()

	res, err := b.QueryClient.EthCall(ctx, &req)
	if err != nil {
		return nil, err
	}

	if err = handleRevertError(res.VmError, res.Ret); err != nil {
		return nil, err
	}

	return res, nil
}
```

**File:** x/vm/keeper/grpc_query.go (L229-273)
```go
// EthCall implements eth_call rpc api.
func (k Keeper) EthCall(c context.Context, req *types.EthCallRequest) (*types.MsgEthereumTxResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "empty request")
	}

	var overrides *rpctypes.StateOverride
	if len(req.Overrides) > 0 {
		overrides = new(rpctypes.StateOverride)
		if err := json.Unmarshal(req.Overrides, overrides); err != nil {
			return nil, status.Error(codes.InvalidArgument, fmt.Sprintf("invalid state overrides format: %s", err.Error()))
		}
	}

	ctx := sdk.UnwrapSDKContext(c)

	var args types.TransactionArgs
	err := json.Unmarshal(req.Args, &args)
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}

	cfg, err := k.EVMConfig(ctx, GetProposerAddress(ctx, req.ProposerAddress))
	if err != nil {
		return nil, status.Error(codes.Internal, err.Error())
	}

	// ApplyMessageWithConfig expect correct nonce set in msg
	nonce := k.GetNonce(ctx, args.GetFrom())
	args.Nonce = (*hexutil.Uint64)(&nonce)

	if err := args.CallDefaults(req.GasCap, cfg.BaseFee, types.GetEthChainConfig().ChainID); err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}

	msg := args.ToMessage(cfg.BaseFee, false, false)
	txConfig := statedb.NewEmptyTxConfig()

	// pass false to not commit StateDB
	res, err := k.ApplyMessageWithConfig(ctx, *msg, nil, false, cfg, txConfig, false, overrides)
	if err != nil {
		return nil, status.Error(codes.Internal, err.Error())
	}

	return res, nil
```
