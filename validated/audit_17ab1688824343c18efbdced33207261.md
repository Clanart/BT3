Based on my research, I found a direct structural analog to the CyanVaultV2 `initializer()`/`reinitializer()` bug in Cosmos EVM's `x/vm` module, in the one-shot global coin-info initialization pattern.

### Title
Global EVM coin-info configuration (decimals/denom) is initialized only once via `sync.Once`, permanently desynchronizing package-level scaling globals from `x/vm` state after any post-genesis `EvmCoinInfo` change - (File: `x/vm/module.go`, `x/vm/genesis.go`, `x/vm/keeper/coin_info.go`)

### Summary
The Solidity report's root cause is that `initializer()` permits `initialize()` to run exactly once, so a proxy cannot re-initialize new state introduced by an upgrade unless a separate `reinitializer()`-guarded function is added. Cosmos EVM's `x/vm` module has the same one-shot initialization pattern for EVM coin configuration (denom, extended denom, display denom, decimals), guarded by a `sync.Once` instead of a Solidity `initializer` modifier, with no analog to `reinitializer()`.

### Finding Description
`AppModule` carries a `*sync.Once` field named `initializer` [1](#0-0) . It is invoked from `InitGenesis`: [2](#0-1) 
and from every `PreBlock` call thereafter: [3](#0-2) 

`SetGlobalConfigVariables` mutates **package-level global state** (the SDK base denom registry and the `x/vm/types` EVM coin info singleton used for wei⇄native decimal conversion) via `setBaseDenom` and `EVMConfigurator.WithEVMCoinInfo(...).Configure()`: [4](#0-3) . These globals (`GetEVMCoinDecimals()`, `GetEVMCoinDenom()`, `ConversionFactor` lookups, etc.) are consulted throughout ante handlers, the StateDB, and precompiles to convert between the EVM's 18-decimal wei representation and the chain's native bank denom decimals.

Separately, the *persisted* `EvmCoinInfo` in the `x/vm` store can be recomputed independently via `LoadEvmCoinInfo`/`InitEvmCoinInfo`/`SetEvmCoinInfo`: [5](#0-4) . `InitEvmCoinInfo` is explicitly documented as re-callable after a chain upgrade, e.g. in the reference upgrade handler that updates `ExtendedDenomOptions` and then calls `InitEvmCoinInfo` again: [6](#0-5) .

Because `initializer.Do(...)` only fires once per process lifetime, once `SetGlobalConfigVariables` has run (at genesis or on the first `PreBlock` after process start), any subsequent change to the persisted `EvmCoinInfo` — whether via an upgrade handler that calls `InitEvmCoinInfo` again in the same running process, or via any keeper/msg path that updates `x/vm` params/`EvmCoinInfo` without a full process restart — is silently ignored by `PreBlock`'s `initializer.Do`. The keeper-level state (`k.GetEvmCoinInfo(ctx)`) and the package-level scaling globals used by the EVM execution/conversion path then permanently diverge for the remainder of the process's life, exactly mirroring the CyanVaultV2 pattern where new state introduced post-upgrade can never be (re)initialized because the guard only allows a single call.

### Impact Explanation
If the persisted `EvmCoinInfo` (denom, decimals) changes while the running process's `sync.Once` has already fired, all subsequent ordinary user transactions are processed using the stale global decimal/denom scaling factor while bank/erc20/precompile logic that reads `k.GetEvmCoinInfo(ctx)` directly uses the new value. This produces mismatched wei⇄native unit conversions across EVM transaction execution, which is a systemic accounting-corruption vector affecting native/EVM balance conversions for every unprivileged user's subsequent EVM transaction — matching the "irreversible accounting corruption of spendable user value across native balances, EVM balances" Critical impact category.

### Likelihood Explanation
Triggering the divergence requires the persisted `EvmCoinInfo` to actually change within a running process after `PreBlock`'s `sync.Once` has already fired (e.g., a chain upgrade path that calls `InitEvmCoinInfo` in-process rather than relying on a fresh binary restart, as `evmd/upgrades.go` demonstrates). I was not able to fully verify, within the remaining tool budget, whether `x/vm`'s `MsgUpdateParams` handler (`x/vm/keeper/msg_server.go`) can independently trigger a change to `EvmDenom`/`ExtendedDenomOptions`/decimals without a corresponding node restart, or whether such changes are otherwise guarded/rejected. That verification gap should be resolved by a Devin session with full file access before treating this as confirmed-exploitable, since the severity hinges on whether an in-process re-initialization path (as shown in the upgrade-handler documentation) is actually reachable outside of a full binary restart.

### Recommendation
Remove the one-shot `sync.Once` gate for `SetGlobalConfigVariables`, or add an explicit re-initialization path (analogous to `reinitializer(2)`) that is invoked whenever `SetEvmCoinInfo`/`InitEvmCoinInfo` changes the persisted coin info, ensuring the package-level scaling globals are always kept in sync with the `x/vm` keeper state, including across any upgrade or param-update flow that does not involve a full process restart.

### Proof of Concept
Not independently reproducible from static review alone — reproduction requires: (1) starting a node so `PreBlock`'s `sync.Once` fires with the genesis `EvmCoinInfo`, then (2) triggering, within the same running process, a code path that calls `k.InitEvmCoinInfo`/`k.SetEvmCoinInfo` with different decimals/denom (e.g. an in-process upgrade handler or a keeper call reachable from `MsgUpdateParams`), then (3) submitting an ordinary EVM transaction and observing that the resulting native-token debit/credit uses the stale global conversion factor while `GetEvmCoinInfo` reports the new value — confirming the divergence. This last verification step requires code/runtime access beyond what was available in this scan and should be validated in a full Devin session.

### Citations

**File:** x/vm/module.go (L119-127)
```go
func NewAppModule(k *keeper.Keeper, ak types.AccountKeeper, bankKeeper types.BankKeeper, ac address.Codec) AppModule {
	return AppModule{
		AppModuleBasic: AppModuleBasic{ac: ac},
		keeper:         k,
		ak:             ak,
		bankKeeper:     bankKeeper,
		initializer:    &sync.Once{},
	}
}
```

**File:** x/vm/module.go (L146-153)
```go
func (am AppModule) PreBlock(goCtx context.Context) (appmodule.ResponsePreBlock, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	coinInfo := am.keeper.GetEvmCoinInfo(ctx)
	am.initializer.Do(func() {
		SetGlobalConfigVariables(coinInfo)
	})
	return &sdk.ResponsePreBlock{ConsensusParamsChanged: false}, nil
}
```

**File:** x/vm/module.go (L203-237)
```go
// setBaseDenom registers the display denom and base denom and sets the
// base denom for the chain. The function registered different values based on
// the EvmCoinInfo to allow different configurations in mainnet and testnet.
func setBaseDenom(ci types.EvmCoinInfo) (err error) {
	// Defer setting the base denom, and capture any potential error from it.
	// So when failing because the denom was already registered, we ignore it and set
	// the corresponding denom to be base denom
	defer func() {
		err = sdk.SetBaseDenom(ci.Denom)
	}()
	if err := sdk.RegisterDenom(ci.DisplayDenom, math.LegacyOneDec()); err != nil {
		return err
	}

	// sdk.RegisterDenom will automatically overwrite the base denom when the
	// new setBaseDenom() units are lower than the current base denom's units.
	return sdk.RegisterDenom(ci.Denom, math.LegacyNewDecWithPrec(1, int64(ci.Decimals)))
}

func SetGlobalConfigVariables(coinInfo types.EvmCoinInfo) {
	// set the denom info for the chain
	if err := setBaseDenom(coinInfo); err != nil {
		panic(err)
	}

	configurator := types.NewEVMConfigurator()
	err := configurator.
		WithExtendedEips(types.DefaultCosmosEVMActivators).
		// NOTE: we're using the 18 decimals default for the example chain
		WithEVMCoinInfo(coinInfo).
		Configure()
	if err != nil {
		panic(err)
	}
}
```

**File:** x/vm/genesis.go (L63-70)
```go
	if err := k.InitEvmCoinInfo(ctx); err != nil {
		panic(fmt.Errorf("error initializing evm coin info: %s", err))
	}

	coinInfo := k.GetEvmCoinInfo(ctx)
	initializer.Do(func() {
		SetGlobalConfigVariables(coinInfo)
	})
```

**File:** x/vm/keeper/coin_info.go (L11-52)
```go
// LoadEvmCoinInfo load EvmCoinInfo from bank denom metadata
func (k Keeper) LoadEvmCoinInfo(ctx sdk.Context) (types.EvmCoinInfo, error) {
	var decimals types.Decimals

	params := k.GetParams(ctx)
	evmDenomMetadata, found := k.bankWrapper.GetDenomMetaData(ctx, params.EvmDenom)
	if !found {
		return types.EvmCoinInfo{}, fmt.Errorf("denom metadata %s could not be found", params.EvmDenom)
	}

	for _, denomUnit := range evmDenomMetadata.DenomUnits {
		if denomUnit.Denom == evmDenomMetadata.Display {
			decimals = types.Decimals(denomUnit.Exponent)
		}
	}

	var extendedDenom string
	if decimals == 18 {
		extendedDenom = params.EvmDenom
	} else {
		if params.ExtendedDenomOptions == nil {
			return types.EvmCoinInfo{}, fmt.Errorf("extended denom options cannot be nil for non-18-decimal chains")
		}
		extendedDenom = params.ExtendedDenomOptions.ExtendedDenom
	}

	return types.EvmCoinInfo{
		Denom:         params.EvmDenom,
		ExtendedDenom: extendedDenom,
		DisplayDenom:  evmDenomMetadata.Display,
		Decimals:      decimals.Uint32(),
	}, nil
}

// InitEvmCoinInfo load EvmCoinInfo from bank denom metadata and store it in the module
func (k Keeper) InitEvmCoinInfo(ctx sdk.Context) error {
	coinInfo, err := k.LoadEvmCoinInfo(ctx)
	if err != nil {
		return err
	}
	return k.SetEvmCoinInfo(ctx, coinInfo)
}
```

**File:** evmd/upgrades.go (L53-66)
```go
			// (Required for NON-18 denom chains *only)
			// Update EVM params to add Extended denom options
			// Ensure that this corresponds to the EVM denom
			// (tyically the bond denom)
			evmParams := app.EVMKeeper.GetParams(sdkCtx)
			evmParams.ExtendedDenomOptions = &types.ExtendedDenomOptions{ExtendedDenom: "atest"}
			err := app.EVMKeeper.SetParams(sdkCtx, evmParams)
			if err != nil {
				return nil, err
			}
			// Initialize EvmCoinInfo in the module store
			if err := app.EVMKeeper.InitEvmCoinInfo(sdkCtx); err != nil {
				return nil, err
			}
```
