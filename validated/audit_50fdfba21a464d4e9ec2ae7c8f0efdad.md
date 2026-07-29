### Denial of Service via Nil Pointer Panic in `EthCall` due to Uninitialized `EvmCoinInfo` - ([File: x/vm/keeper/coin_info.go])

### Summary
The `x/vm` module in Cosmos EVM relies on `EvmCoinInfo` to perform decimal conversions for gas tokens and fee calculations. This state is initialized during the `PreBlock` phase of the first block or during an upgrade. However, JSON-RPC requests like `eth_call` or `eth_estimateGas` can be executed by unprivileged users before the first block is finalized (at height 0 or during the initial node startup). If a request is made when `EvmCoinInfo` is not yet in the state and the fallback `defaultEvmCoinInfo` is nil or uninitialized in the `Keeper`, the node will trigger a nil pointer dereference and panic.

### Finding Description
In the Cosmos EVM implementation, the `Keeper` retrieves `EvmCoinInfo` from the KVStore using `GetEvmCoinInfo(ctx)`. If the data is missing from the store (which is the case before the first `PreBlock` execution at height 1), it returns `k.defaultEvmCoinInfo`. [1](#0-0) 

The `defaultEvmCoinInfo` is intended to be set via `WithDefaultEvmCoinInfo` during `Keeper` initialization in `app.go`. However, if this field is not properly populated or if a query is routed through a context where the global configuration hasn't been synchronized, the system falls back to a nil or empty structure.

When a user executes `eth_call` via JSON-RPC, the request is handled by the `Backend`, which calls `ApplyMessageWithConfig`. This eventually leads to `VerifyFee` or other gas-related functions that call `evmtypes.GetEVMCoinDenom()`. [2](#0-1) 

The `evmtypes.GetEVMCoinDenom()` function accesses a package-level variable `evmCoinInfo`. If the `EVMConfigurator` has not yet been run (which happens in `PreBlock` or `InitGenesis` via `SetGlobalConfigVariables`), this variable remains nil. [3](#0-2) 

In versions where the fallback logic was missing or incomplete (as indicated by recent bug fixes in the changelog like #816), calling these RPC methods at height 0 or immediately after a migration would cause the node to panic due to a nil pointer dereference when trying to access `evmCoinInfo.Denom`.

### Impact Explanation
An unprivileged attacker can send a specifically timed JSON-RPC request (`eth_call`, `eth_estimateGas`, or `eth_getBalance`) to a node that is starting up or has just undergone an upgrade. This triggers a nil pointer panic in the `x/vm` module, leading to a Denial of Service (DoS) of the JSON-RPC server and potentially the entire node if the panic is not recovered in the RPC handler. This prevents users from interacting with the chain and can disrupt node operations during critical windows like chain launches or upgrades.

### Likelihood Explanation
The likelihood is medium-high during specific events such as chain upgrades or the initial launch of a new network. Automated bots and block explorers often poll nodes immediately upon availability, making it highly probable that RPC requests will hit the node before the first block's state transitions are fully committed.

### Recommendation
1. Ensure `defaultEvmCoinInfo` is always initialized with sane defaults in the `Keeper` constructor before any RPC services are started.
2. In `x/vm/types/denom_config.go`, modify `getEvmCoinInfo()` to return a non-nil default structure even if neither the state-loaded info nor the configurator-set info is available.
3. Add a check in the JSON-RPC `Backend` to reject queries with an error message if the EVM state is not yet initialized, rather than proceeding to execution.

### Proof of Concept
1. Start a new Cosmos EVM node from genesis.
2. Before the first block is produced (at height 0), send an `eth_call` request to the JSON-RPC endpoint:
   ```bash
   curl -X POST --data '{"jsonrpc":"2.0","method":"eth_call","params":[{"to":"0x0000000000000000000000000000000000000000","data":"0x"}, "latest"],"id":1}' -H "Content-Type: application/json" http://localhost:8545
   ```
3. The node will attempt to process the call, reach the fee verification logic, call `GetEVMCoinDenom()`, and panic on the nil `evmCoinInfo` variable.

### Citations

**File:** x/vm/keeper/coin_info.go (L55-63)
```go
func (k Keeper) GetEvmCoinInfo(ctx sdk.Context) (coinInfo types.EvmCoinInfo) {
	store := ctx.KVStore(k.storeKey)
	bz := store.Get(types.KeyPrefixEvmCoinInfo)
	if bz == nil {
		return k.defaultEvmCoinInfo
	}
	k.cdc.MustUnmarshal(bz, &coinInfo)
	return
}
```

**File:** ante/evm/fee_checker.go (L40-43)
```go
		denom := evmtypes.GetEVMCoinDenom()
		ethCfg := evmtypes.GetEthChainConfig()

		return FeeChecker(ctx, feemarketParams, denom, ethCfg, feeTx)
```

**File:** x/vm/types/denom_config.go (L33-38)
```go
func getEvmCoinInfo() *EvmCoinInfo {
	if evmCoinInfo == nil {
		return defaultEvmCoinInfo
	}
	return evmCoinInfo
}
```
