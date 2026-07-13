### Title
Stale `feemarketParams` Captured in `NewDynamicFeeChecker` Closure Bypasses Base Fee Enforcement After Governance Parameter Update — (File: `ante/evm/fee_checker.go`)

---

### Summary

`NewDynamicFeeChecker` captures `feemarketParams` by pointer at ante handler construction time. If feemarket parameters are updated via governance (e.g., `NoBaseFee` toggled, `MinGasPrice` raised), the closure continues to evaluate fees against the stale snapshot, allowing Cosmos/EIP-712 transactions with fees below the current base fee to pass ante validation.

---

### Finding Description

In `ante/evm/fee_checker.go`, `NewDynamicFeeChecker` accepts `feemarketParams *feemarkettypes.Params` and closes over it permanently:

```go
func NewDynamicFeeChecker(ethCfg *params.ChainConfig, evmParams *types.Params, feemarketParams *feemarkettypes.Params) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        ...
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)  // uses captured pointer
        if baseFee == nil {
            return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)               // falls back silently
        }
``` [1](#0-0) 

The caller, `newLegacyCosmosAnteHandlerEip712`, reads params once at handler construction time and passes their addresses into the closure:

```go
evmParams := options.EvmKeeper.GetParams(ctx)
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
...
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [2](#0-1) 

`evmParams` and `feemarketParams` are stack-local variables whose addresses are captured by the closure. Go's escape analysis promotes them to the heap, but they are **never refreshed** from the KV store after construction. Any subsequent governance `MsgUpdateParams` that writes a new `Params` struct to the store is invisible to the closure, which still dereferences the original allocation.

The fields that control base-fee enforcement are read exclusively from this stale pointer:

- `feemarketParams.NoBaseFee` — gates whether `GetBaseFee` returns a non-nil value at all.
- `feemarketParams.EnableHeight` — gates `IsBaseFeeEnabled`.
- `evmParams.EvmDenom` — determines which coin denomination is checked. [3](#0-2) 

---

### Impact Explanation

**Scenario A — `NoBaseFee` toggled false → true after governance (base fee disabled post-update):**
The closure still sees `NoBaseFee = false`, so `GetBaseFee` returns a non-nil value and the checker enforces the (now-obsolete) base fee. Legitimate transactions that correctly omit the base fee are rejected, causing a liveness degradation for all EIP-712 Cosmos transactions.

**Scenario B — `NoBaseFee` toggled true → false after governance (base fee enabled post-update):**
The closure still sees `NoBaseFee = true`, so `GetBaseFee` returns `nil` and the checker silently falls back to `checkTxFeeWithValidatorMinGasPrices`. An unprivileged sender can submit EIP-712 Cosmos transactions with a `gasPrice` that satisfies only the (potentially zero) validator min-gas-price, entirely bypassing the newly-activated EIP-1559 base fee. Fees are mis-accounted: the fee collector receives less than the protocol-mandated minimum, and the base-fee burn/distribution is skipped. [4](#0-3) 

This matches the allowed High impact: *"fee market, ante handler … bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

Governance parameter updates to `x/feemarket` are a normal, documented operational action (e.g., enabling EIP-1559 on a chain that launched with `NoBaseFee = true`). The ante handler is constructed once at app startup; no code path refreshes the captured pointer on a governance execution. Any transaction submitted in the window after governance execution and before a node restart exploits the stale state. Because node restarts are not guaranteed to be immediate, the window can span many blocks.

---

### Recommendation

Remove the captured-at-construction parameters and instead read them fresh from the keepers inside the closure on every invocation:

```go
func NewDynamicFeeChecker(
    evmKeeper EVMKeeperI,
    feemarketKeeper FeeMarketKeeperI,
) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        evmParams      := evmKeeper.GetParams(ctx)
        feemarketParams := feemarketKeeper.GetParams(ctx)
        ethCfg := evmParams.GetChainConfig().EthereumConfig(evmKeeper.ChainID())
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, &feemarketParams)
        ...
    }
}
```

This mirrors how `ApplyTransaction` already loads a fresh `EVMConfig` (including `FeeMarketParams`) per transaction via `k.EVMConfig(ctx, ...)`, ensuring governance updates are reflected immediately. [5](#0-4) 

---

### Proof of Concept

1. Chain launches with `x/feemarket` params `NoBaseFee = true` (base fee disabled). The ante handler is constructed; the closure captures `feemarketParams.NoBaseFee = true`.
2. Governance proposal passes: `MsgUpdateParams` sets `NoBaseFee = false`, activating EIP-1559 base fee. The KV store is updated; `CalculateBaseFee` now returns a non-zero value each block.
3. The `NewDynamicFeeChecker` closure still holds the old pointer where `NoBaseFee = true`.
4. `types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)` evaluates `IsBaseFeeEnabled` against the stale params and returns `nil`.
5. The checker falls through to `checkTxFeeWithValidatorMinGasPrices`, which only enforces the local validator `min-gas-prices` (often zero on public networks).
6. An attacker broadcasts EIP-712 Cosmos transactions with `gasPrice = 0` (or any value below the active base fee). They pass ante validation, are included in blocks, and pay zero base fee — mis-accounting protocol fees for every such transaction. [6](#0-5) [2](#0-1)

### Citations

**File:** ante/evm/fee_checker.go (L42-60)
```go
func NewDynamicFeeChecker(ethCfg *params.ChainConfig, evmParams *types.Params, feemarketParams *feemarkettypes.Params) authante.TxFeeChecker {
	return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
		feeTx, ok := tx.(sdk.FeeTx)
		if !ok {
			return nil, 0, fmt.Errorf("tx must be a FeeTx")
		}

		if ctx.BlockHeight() == 0 {
			// genesis transactions: fallback to min-gas-price logic
			return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)
		}

		denom := evmParams.EvmDenom

		baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
		if baseFee == nil {
			// london hardfork is not enabled: fallback to min-gas-prices logic
			return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)
		}
```

**File:** evmd/ante/evm_handler.go (L29-38)
```go
	evmParams := options.EvmKeeper.GetParams(ctx)
	feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
	evmDenom := evmParams.EvmDenom
	chainID := options.EvmKeeper.ChainID()
	chainCfg := evmParams.GetChainConfig()
	ethCfg := chainCfg.EthereumConfig(chainID)
	var txFeeChecker authante.TxFeeChecker
	if options.DynamicFeeChecker {
		txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
	}
```

**File:** x/evm/keeper/state_transition.go (L163-170)
```go
func (k *Keeper) ApplyTransaction(ctx sdk.Context, msgEth *types.MsgEthereumTx) (*types.EVMResult, error) {
	ethTx := msgEth.AsTransaction()
	cfg, err := k.EVMConfig(ctx, k.eip155ChainID, ethTx.Hash())
	if err != nil {
		return nil, errorsmod.Wrap(err, "failed to load evm config")
	}

	msg := msgEth.AsMessage(cfg.BaseFee)
```
