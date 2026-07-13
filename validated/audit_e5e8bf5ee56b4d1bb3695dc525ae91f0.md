### Title
Stale `EVMBlockConfig` Object-Store Cache After Mid-Block `MsgUpdateParams` Causes Fee Mis-Accounting - (File: `x/evm/keeper/config.go`)

### Summary
`EVMBlockConfig` — which caches `BaseFee`, `FeeMarketParams`, `Params` (including `EvmDenom`, `EnableCreate`, `EnableCall`, `ChainConfig`), `Rules`, and `DefaultPrecompiles` — is written to the EVM object store on first access within a block and is only cleared in `EndBlock`. Neither `evm.UpdateParams` nor `feemarket.UpdateParams` invalidates this cache. When a governance `MsgUpdateParams` transaction executes mid-block, all subsequent EVM transactions in the same block use stale cached params, causing fee mis-accounting and incorrect EVM rule enforcement.

### Finding Description

`EVMBlockConfig()` populates the cache on first call and stores it under `KeyPrefixObjectParams` in the object store:

```go
// x/evm/keeper/config.go:75-134
func (k *Keeper) EVMBlockConfig(ctx sdk.Context, chainID *big.Int) (*EVMBlockConfig, error) {
    objStore := ctx.ObjectStore(k.objectKey)
    v := objStore.Get(types.KeyPrefixObjectParams)
    if v != nil {
        return v.(*EVMBlockConfig), nil   // ← returns stale cache
    }
    ...
    objStore.Set(types.KeyPrefixObjectParams, cfg)
    return cfg, nil
}
``` [1](#0-0) 

The cache is cleared **only** in `EndBlock`:

```go
// x/evm/keeper/abci.go:50-53
func (k *Keeper) EndBlock(ctx sdk.Context) error {
    k.CollectTxBloom(ctx)
    k.RemoveParamsCache(ctx)   // ← only here
    return nil
}
``` [2](#0-1) 

`evm.UpdateParams` writes new params to the KV store but **never** calls `RemoveParamsCache`:

```go
// x/evm/keeper/msg_server.go:143-154
func (k *Keeper) UpdateParams(goCtx context.Context, req *types.MsgUpdateParams) (*types.MsgUpdateParamsResponse, error) {
    ...
    ctx := sdk.UnwrapSDKContext(goCtx)
    if err := k.SetParams(ctx, req.Params); err != nil {   // ← KV store only
        return nil, err
    }
    return &types.MsgUpdateParamsResponse{}, nil
}
``` [3](#0-2) 

`feemarket.UpdateParams` similarly writes to the feemarket KV store without invalidating the EVM object-store cache:

```go
// x/feemarket/keeper/msg_server.go:16-27
func (k *Keeper) UpdateParams(goCtx context.Context, req *types.MsgUpdateParams) (*types.MsgUpdateParamsResponse, error) {
    ...
    ctx := sdk.UnwrapSDKContext(goCtx)
    if err := k.SetParams(ctx, req.Params); err != nil {   // ← KV store only
        return nil, err
    }
    return &types.MsgUpdateParamsResponse{}, nil
}
``` [4](#0-3) 

The ante handler reads `EVMBlockConfig` from the object-store cache and uses the cached `baseFee` for all fee validation:

```go
// evmd/ante/handler_options.go:88-141
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
...
baseFee := blockCfg.BaseFee
...
if err := cosmos.CheckEthMempoolFee(ctx, tx, simulate, baseFee, evmDenom); err != nil { ... }
if err := cosmos.CheckEthMinGasPrice(tx, feemarketParams.MinGasPrice, baseFee); err != nil { ... }
...
ctx, err = evmante.CheckEthGasConsume(ctx, tx, rules, options.EvmKeeper, baseFee, evmDenom)
``` [5](#0-4) 

`ApplyTransaction` also reads the cached config and uses `cfg.BaseFee` to compute the effective gas price for fee deduction and refund:

```go
// x/evm/keeper/state_transition.go:165-170
cfg, err := k.EVMConfig(ctx, k.eip155ChainID, ethTx.Hash())
...
msg := msgEth.AsMessage(cfg.BaseFee)   // ← stale BaseFee used for effective price
``` [6](#0-5) 

Gas refund also uses the stale denom from the cached params:

```go
// x/evm/keeper/state_transition.go:252
if err = k.RefundGas(ctx, msg, leftoverGas, cfg.Params.EvmDenom); err != nil {
``` [7](#0-6) 

### Impact Explanation

**Fee mis-accounting (BaseFee change):** If governance executes `feemarket.MsgUpdateParams` raising `BaseFee` from 1000 to 2000 mid-block, subsequent EVM transactions with `gasFeeCap` between 1000 and 2000 pass the ante handler (stale cache says BaseFee=1000) and commit with fees below the new protocol minimum. Conversely, lowering `BaseFee` causes valid transactions to be incorrectly rejected.

**Wrong denom for fees/refunds (EvmDenom change):** If `evm.MsgUpdateParams` changes `EvmDenom`, subsequent transactions deduct fees and issue refunds in the old denom while the new denom is stored in KV, permanently mis-accounting user balances.

**Access control bypass (EnableCreate/EnableCall change):** If governance disables `EnableCreate` or `EnableCall`, subsequent transactions in the same block bypass the restriction because the cached params still show them enabled.

**Wrong EVM rules/precompiles (ChainConfig change):** If `ChainConfig` is updated (e.g., enabling a new hardfork), the cached `Rules` and `DefaultPrecompiles` are stale, causing transactions to execute under incorrect EVM semantics.

This matches the allowed impact: *"EVM state transition, gas refund, fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

**Medium.** Governance proposals that update `x/evm` or `x/feemarket` params are routine on-chain operations. When a proposal passes, its `MsgUpdateParams` is included in a block alongside other transactions. Any EVM transactions in the same block after the governance message are affected. This is not a contrived scenario — it is the normal execution path for every governance parameter update.

### Recommendation

Both `evm.UpdateParams` and `feemarket.UpdateParams` must invalidate the EVM object-store cache immediately after writing new params to the KV store:

```go
// In evm/keeper/msg_server.go UpdateParams:
if err := k.SetParams(ctx, req.Params); err != nil {
    return nil, err
}
k.RemoveParamsCache(ctx)   // ← add this

// In feemarket/keeper/msg_server.go UpdateParams:
if err := k.SetParams(ctx, req.Params); err != nil {
    return nil, err
}
evmKeeper.RemoveParamsCache(ctx)   // ← add this (requires feemarket keeper to hold a reference, or use a hook)
```

Alternatively, `evm.SetParams` and `feemarket.SetParams` should automatically call `RemoveParamsCache` so no caller can forget.

### Proof of Concept

1. Block N begins. `evm.BeginBlock` calls `EVMBlockConfig`, which reads `feemarketParams.BaseFee = 1000` from KV store and caches it in the object store.
2. **Tx 1** (governance): `feemarket.MsgUpdateParams` executes with `BaseFee = 5000`. `feemarket.SetParams` writes `BaseFee = 5000` to the feemarket KV store. The EVM object-store cache is **not** invalidated; it still holds `BaseFee = 1000`.
3. **Tx 2** (user EVM tx): User submits a `DynamicFeeTx` with `gasFeeCap = 2000`, `gasTipCap = 0`. The ante handler calls `EVMBlockConfig` → returns cached config with `BaseFee = 1000`. `CheckEthGasConsume` computes `effectivePrice = min(0 + 1000, 2000) = 1000` and deducts `1000 * gasLimit` from the user. Transaction is accepted and committed.
4. **Result**: The transaction committed with an effective fee of `1000 * gasLimit`, which is below the new protocol minimum of `5000 * gasLimit`. The fee market invariant is violated; the user paid far less than required, and the fee collector received less than it should have. [8](#0-7) [2](#0-1) [3](#0-2) [4](#0-3) [9](#0-8) [6](#0-5)

### Citations

**File:** x/evm/keeper/config.go (L75-139)
```go
func (k *Keeper) EVMBlockConfig(ctx sdk.Context, chainID *big.Int) (*EVMBlockConfig, error) {
	objStore := ctx.ObjectStore(k.objectKey)
	v := objStore.Get(types.KeyPrefixObjectParams)
	if v != nil {
		return v.(*EVMBlockConfig), nil
	}

	params := k.GetParams(ctx)
	ethCfg := params.ChainConfig.EthereumConfig(chainID)

	feemarketParams := k.feeMarketKeeper.GetParams(ctx)

	// get the coinbase address from the block proposer
	coinbase, err := k.GetCoinbaseAddress(ctx)
	if err != nil {
		return nil, errorsmod.Wrap(err, "failed to obtain coinbase address")
	}

	var baseFee *big.Int
	if types.IsLondon(ethCfg, ctx.BlockHeight()) {
		baseFee = feemarketParams.GetBaseFee()
		// should not be nil if london hardfork enabled
		if baseFee == nil {
			baseFee = new(big.Int)
		}
	}
	time := ctx.BlockHeader().Time
	var blockTime uint64
	if !time.IsZero() {
		blockTime, err = ethermint.SafeUint64(time.Unix())
		if err != nil {
			return nil, err
		}
	}
	blockNumber := big.NewInt(ctx.BlockHeight())
	rules := ethCfg.Rules(blockNumber, ethCfg.MergeNetsplitBlock != nil, blockTime)

	// Build the default precompile set once per block.
	contracts := make(map[common.Address]vm.PrecompiledContract)
	for addr, c := range vm.DefaultPrecompiles(rules) {
		contracts[addr] = c
	}

	var zero common.Hash
	cfg := &EVMBlockConfig{
		Params:             params,
		FeeMarketParams:    feemarketParams,
		ChainConfig:        ethCfg,
		CoinBase:           coinbase,
		BaseFee:            baseFee,
		Difficulty:         new(big.Int),
		Random:             &zero,
		BlobBaseFee:        new(big.Int),
		BlockNumber:        blockNumber,
		BlockTime:          blockTime,
		Rules:              rules,
		DefaultPrecompiles: contracts,
	}
	objStore.Set(types.KeyPrefixObjectParams, cfg)
	return cfg, nil
}

func (k *Keeper) RemoveParamsCache(ctx sdk.Context) {
	ctx.ObjectStore(k.objectKey).Delete(types.KeyPrefixObjectParams)
}
```

**File:** x/evm/keeper/abci.go (L50-53)
```go
func (k *Keeper) EndBlock(ctx sdk.Context) error {
	k.CollectTxBloom(ctx)
	k.RemoveParamsCache(ctx)
	return nil
```

**File:** x/evm/keeper/msg_server.go (L143-154)
```go
func (k *Keeper) UpdateParams(goCtx context.Context, req *types.MsgUpdateParams) (*types.MsgUpdateParamsResponse, error) {
	if k.authority.String() != req.Authority {
		return nil, errorsmod.Wrapf(govtypes.ErrInvalidSigner, "invalid authority, expected %s, got %s", k.authority.String(), req.Authority)
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := k.SetParams(ctx, req.Params); err != nil {
		return nil, err
	}

	return &types.MsgUpdateParamsResponse{}, nil
}
```

**File:** x/feemarket/keeper/msg_server.go (L16-27)
```go
func (k *Keeper) UpdateParams(goCtx context.Context, req *types.MsgUpdateParams) (*types.MsgUpdateParamsResponse, error) {
	if k.authority.String() != req.Authority {
		return nil, errorsmod.Wrapf(govtypes.ErrInvalidSigner, "invalid authority; expected %s, got %s", k.authority.String(), req.Authority)
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := k.SetParams(ctx, req.Params); err != nil {
		return nil, err
	}

	return &types.MsgUpdateParamsResponse{}, nil
}
```

**File:** evmd/ante/handler_options.go (L88-141)
```go
		blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
		if err != nil {
			return ctx, errorsmod.Wrap(errortypes.ErrLogic, err.Error())
		}
		evmParams := &blockCfg.Params
		evmDenom := evmParams.EvmDenom
		feemarketParams := &blockCfg.FeeMarketParams
		baseFee := blockCfg.BaseFee
		rules := blockCfg.Rules

		// all transactions must implement FeeTx
		_, ok := tx.(sdk.FeeTx)
		if !ok {
			return ctx, errorsmod.Wrapf(errortypes.ErrInvalidType, "invalid transaction type %T, expected sdk.FeeTx", tx)
		}

		// We need to setup an empty gas config so that the gas is consistent with Ethereum.
		ctx, err = interfaces.SetupEthContext(ctx)
		if err != nil {
			return ctx, err
		}

		if err := cosmos.CheckEthMempoolFee(ctx, tx, simulate, baseFee, evmDenom); err != nil {
			return ctx, err
		}

		if err := cosmos.CheckEthMinGasPrice(tx, feemarketParams.MinGasPrice, baseFee); err != nil {
			return ctx, err
		}

		if err := interfaces.ValidateEthBasic(ctx, tx, evmParams, baseFee); err != nil {
			return ctx, err
		}

		ethSigner := ethtypes.MakeSigner(blockCfg.ChainConfig, blockCfg.BlockNumber, blockCfg.BlockTime)
		if err := evmante.VerifyEthSig(tx, ethSigner); err != nil {
			return ctx, err
		}

		// AccountGetter cache the account objects during the ante handler execution,
		// it's safe because there's no store branching in the ante handlers.
		accountGetter := evmante.NewCachedAccountGetter(ctx, options.AccountKeeper)

		if err := evmante.VerifyEthAccount(ctx, tx, options.EvmKeeper, evmDenom, accountGetter, rules); err != nil {
			return ctx, err
		}

		if err := evmante.CheckEthCanTransfer(ctx, tx, baseFee, rules, options.EvmKeeper, evmParams); err != nil {
			return ctx, err
		}

		ctx, err = evmante.CheckEthGasConsume(
			ctx, tx, rules, options.EvmKeeper,
			baseFee, evmDenom,
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

**File:** x/evm/keeper/state_transition.go (L252-253)
```go
	if err = k.RefundGas(ctx, msg, leftoverGas, cfg.Params.EvmDenom); err != nil {
		return nil, errorsmod.Wrapf(err, "failed to refund leftover gas to sender %s", msg.From)
```
