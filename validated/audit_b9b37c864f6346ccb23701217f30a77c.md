### Title
Migration v5–v8 Fail to Initialize `HeaderHashNum` and `HistoryServeWindow` Params — Uninitialized Fields Left at Zero After Chain Upgrade - (File: `x/evm/migrations/v8/migrate.go`, `x/evm/migrations/v7/migrate.go`, `x/evm/migrations/v6/migrate.go`, `x/evm/migrations/v5/migrate.go`)

---

### Summary

Every in-place store migration in the `x/evm` module (v5 through v8) reads the existing `Params` from the store, modifies only the fields it was written to handle, and writes the result back. Two fields that were added to the `Params` proto message — `HeaderHashNum` (field 7) and `HistoryServeWindow` (field 8) — are never explicitly set by any migration. Because proto3 encodes absent `uint64` fields as zero, any chain that upgrades from a pre-existing state will have both fields equal to `0` in the live store, even though `DefaultParams()` sets them to `256` and `8191` respectively. This is the direct Go/Cosmos analog of the Solidity "immutable variable not set in constructor" pattern: a critical module-level parameter is silently left at its default zero value after an upgrade, causing incorrect EVM execution for every subsequent block.

---

### Finding Description

**New fields, no migration initialization.**

`Params` proto fields 7 (`header_hash_num`) and 8 (`history_serve_window`) were introduced alongside EIP-2935 support: [1](#0-0) [2](#0-1) 

`DefaultParams()` correctly initialises them: [3](#0-2) 

However, every migration only touches the fields it was specifically written for and never sets `HeaderHashNum` or `HistoryServeWindow`:

- **v5** (`Migrate4to5`): consolidates separate param keys, never sets either field. [4](#0-3) 
- **v6** (`Migrate5to6`): adds `ShanghaiTime` only. [5](#0-4) 
- **v7** (`Migrate6to7`): adds `CancunTime` and `PragueTime` only. [6](#0-5) 
- **v8** (`Migrate7to8`): adds `OsakaTime` only, still does not set either field. [7](#0-6) 

`Params.Validate()` accepts `0` for both fields (only checks for `int64` overflow): [8](#0-7) 

So the migration succeeds, the chain starts, and both fields are `0` in the live store.

Additionally, `NewParams()` — the non-default constructor — also omits both fields: [9](#0-8) 

**Consequence 1 — `HeaderHashNum = 0` corrupts `BeginBlock` header-hash bookkeeping.**

`BeginBlock` calls `SetHeaderHash` (stores the current block hash) and then immediately computes the deletion target: [10](#0-9) 

The deletion index is `ctx.BlockHeight() - headerHashNum`. With `headerHashNum = 0` this equals `ctx.BlockHeight()` — the hash that was just written is deleted on every block: [11](#0-10) 

**Consequence 2 — `HeaderHashNum = 0` makes `GetHashFn` return `common.Hash{}` for all historical blocks.**

`GetHashFn` computes the lower bound of the accessible window as `upper - headerHashNum`. With `headerHashNum = 0`, `lower = upper`, so the condition `num64 >= lower` is never satisfied for any `num64 < upper`. The fallback to `GetHistoricalInfo` is never reached, and the function returns `common.Hash{}` (all-zeros) for every historical block number: [12](#0-11) 

This means the EVM `BLOCKHASH` opcode returns `0x000…000` for every queried block after the upgrade.

---

### Impact Explanation

`BLOCKHASH` returning a predictable all-zero value for every block is an EVM state-transition correctness failure with direct security consequences:

1. **Smart-contract randomness exploitation.** Any on-chain contract that derives randomness from `BLOCKHASH` (lottery, NFT mint, commit-reveal scheme) becomes trivially predictable. An attacker who knows `BLOCKHASH` always returns zero can pre-compute winning outcomes and drain contract balances — a direct unauthorized fund transfer through Ethermint transaction execution.

2. **Consensus non-determinism / state divergence.** Nodes that ran the chain before the upgrade (and cached correct hashes) versus nodes that joined after (and see zero) will compute different EVM execution results for any transaction that reads `BLOCKHASH`. This is a deterministic consensus failure path.

3. **`HistoryServeWindow = 0`** is partially mitigated by a fallback in `SetHeaderHash` (`if params.HistoryServeWindow > 0 { window = params.HistoryServeWindow }`), but `HeaderHashNum` has no such fallback anywhere in the hot path.

---

### Likelihood Explanation

Every chain that:
- Was deployed before `HeaderHashNum`/`HistoryServeWindow` were added to the proto, **and**
- Has run any of the v5–v8 migrations

will have `HeaderHashNum = 0` in its live store. This is the normal upgrade path for any long-running Ethermint-based chain. No special attacker action is required to trigger the root cause; the attacker only needs to submit a transaction to any contract that reads `BLOCKHASH`.

---

### Recommendation

Add explicit initialization of `HeaderHashNum` and `HistoryServeWindow` to their canonical defaults in the v8 migration (or introduce a v9 migration):

```go
// In x/evm/migrations/v8/migrate.go (or a new v9/migrate.go)
if params.HeaderHashNum == 0 {
    params.HeaderHashNum = types.DefaultHeaderHashNum   // 256
}
if params.HistoryServeWindow == 0 {
    params.HistoryServeWindow = types.DefaultHistoryServeWindow // 8191
}
```

Also fix `NewParams()` to accept and propagate these fields so it cannot silently produce a zero-valued `Params`: [9](#0-8) 

Add a `Validate()` check that rejects `HeaderHashNum == 0` to prevent silent misconfiguration from reaching consensus.

---

### Proof of Concept

1. Deploy a chain at consensus version ≤ 7 (pre-OsakaTime). `HeaderHashNum` is absent from the serialised params bytes; proto3 decodes it as `0`.
2. Upgrade to consensus version 8. `MigrateStore` runs, adds `OsakaTime = 0`, calls `params.Validate()` (passes, since `ValidateInt64Overflow(0)` is valid), and writes params back — still with `HeaderHashNum = 0`.
3. On the next block, `BeginBlock` executes:
   - `SetHeaderHash` writes `hash(block N)` to the KV store at key `GetHeaderHashKey(N)`.
   - `headerHashNum = cfg.Params.GetHeaderHashNum()` → `0`.
   - `i = ctx.BlockHeight() - 0 = N` → `DeleteHeaderHash(ctx, N)` erases the hash just written.
4. Any EVM transaction in block N+1 that executes `BLOCKHASH(N)` calls `GetHashFn`:
   - `lower = upper - 0 = upper = N+1`.
   - `num64 = N < lower = N+1` → `num64 >= lower` is false.
   - `GetHeaderHash(ctx, N)` returns `common.Hash{}` (deleted in step 3).
   - Returns `common.Hash{}` (all zeros).
5. A lottery contract seeded with `blockhash(block.number - 1)` now always receives `0x000…000`, making every draw trivially winnable by any attacker who submits a transaction in the same block. [13](#0-12) [10](#0-9) [14](#0-13)

### Citations

**File:** proto/ethermint/evm/v1/params.proto (L26-29)
```text
  uint64 header_hash_num = 7;
  // historyServeWindow for EIP 2935
  uint64 history_serve_window = 8;

```

**File:** x/evm/types/params.go (L38-41)
```go
	// DefaultHeaderHashNum defines the default number of header hash to persist.
	DefaultHeaderHashNum = uint64(256)
	// DefaultHistoryServeWindow DefaultHeaderHashNum defines the default number of hystorical value to serve for EIP2935.
	DefaultHistoryServeWindow = uint64(8191)
```

**File:** x/evm/types/params.go (L44-54)
```go
// NewParams creates a new Params instance
func NewParams(evmDenom string, allowUnprotectedTxs, enableCreate, enableCall bool, config ChainConfig, extraEIPs []int64) Params {
	return Params{
		EvmDenom:            evmDenom,
		AllowUnprotectedTxs: allowUnprotectedTxs,
		EnableCreate:        enableCreate,
		EnableCall:          enableCall,
		ExtraEIPs:           extraEIPs,
		ChainConfig:         config,
	}
}
```

**File:** x/evm/types/params.go (L58-68)
```go
func DefaultParams() Params {
	config := DefaultChainConfig()
	return Params{
		EvmDenom:            DefaultEVMDenom,
		EnableCreate:        DefaultEnableCreate,
		EnableCall:          DefaultEnableCall,
		ChainConfig:         config,
		AllowUnprotectedTxs: DefaultAllowUnprotectedTxs,
		HeaderHashNum:       DefaultHeaderHashNum,
		HistoryServeWindow:  DefaultHistoryServeWindow,
	}
```

**File:** x/evm/types/params.go (L93-99)
```go
	if err := ValidateInt64Overflow(p.HeaderHashNum); err != nil {
		return err
	}

	if err := ValidateInt64Overflow(p.HistoryServeWindow); err != nil {
		return err
	}
```

**File:** x/evm/migrations/v5/migrate.go (L16-48)
```go
func MigrateStore(
	ctx sdk.Context,
	storeKey storetypes.StoreKey,
	cdc codec.BinaryCodec,
) error {
	var (
		chainConfig v0types.V0ChainConfig
		extraEIPs   v4types.ExtraEIPs
		params      v4types.V4Params
	)
	store := ctx.KVStore(storeKey)
	chainCfgBz := store.Get(v0types.ParamStoreKeyChainConfig)
	cdc.MustUnmarshal(chainCfgBz, &chainConfig)
	params.ChainConfig = chainConfig
	extraEIPsBz := store.Get(v0types.ParamStoreKeyExtraEIPs)
	cdc.MustUnmarshal(extraEIPsBz, &extraEIPs)
	params.ExtraEIPs = extraEIPs
	params.EvmDenom = string(store.Get(v0types.ParamStoreKeyEVMDenom))
	params.EnableCreate = store.Has(v0types.ParamStoreKeyEnableCreate)
	params.EnableCall = store.Has(v0types.ParamStoreKeyEnableCall)
	params.AllowUnprotectedTxs = store.Has(v0types.ParamStoreKeyAllowUnprotectedTxs)
	if err := params.Validate(); err != nil {
		return err
	}
	bz := cdc.MustMarshal(&params)
	store.Set(types.KeyPrefixParams, bz)
	store.Delete(v0types.ParamStoreKeyChainConfig)
	store.Delete(v0types.ParamStoreKeyExtraEIPs)
	store.Delete(v0types.ParamStoreKeyEVMDenom)
	store.Delete(v0types.ParamStoreKeyEnableCreate)
	store.Delete(v0types.ParamStoreKeyEnableCall)
	store.Delete(v0types.ParamStoreKeyAllowUnprotectedTxs)
	return nil
```

**File:** x/evm/migrations/v6/migrate.go (L15-35)
```go
func MigrateStore(
	ctx sdk.Context,
	storeKey storetypes.StoreKey,
	cdc codec.BinaryCodec,
) error {
	var (
		params    v4types.V4Params
		newParams types.Params
	)
	store := ctx.KVStore(storeKey)
	bz := store.Get(types.KeyPrefixParams)
	cdc.MustUnmarshal(bz, &params)
	newParams = params.ToParams()
	shanghaiTime := sdkmath.ZeroInt()
	newParams.ChainConfig.ShanghaiTime = &shanghaiTime
	if err := newParams.Validate(); err != nil {
		return err
	}
	bz = cdc.MustMarshal(&newParams)
	store.Set(types.KeyPrefixParams, bz)
	return nil
```

**File:** x/evm/migrations/v7/migrate.go (L14-31)
```go
func MigrateStore(
	ctx sdk.Context,
	storeKey storetypes.StoreKey,
	cdc codec.BinaryCodec,
) error {
	var params types.Params
	store := ctx.KVStore(storeKey)
	bz := store.Get(types.KeyPrefixParams)
	cdc.MustUnmarshal(bz, &params)
	zeroInt := sdkmath.ZeroInt()
	params.ChainConfig.CancunTime = &zeroInt
	params.ChainConfig.PragueTime = &zeroInt
	if err := params.Validate(); err != nil {
		return err
	}
	bz = cdc.MustMarshal(&params)
	store.Set(types.KeyPrefixParams, bz)
	return nil
```

**File:** x/evm/migrations/v8/migrate.go (L13-30)
```go
func MigrateStore(
	ctx sdk.Context,
	storeKey storetypes.StoreKey,
	cdc codec.BinaryCodec,
) error {
	var params types.Params
	store := ctx.KVStore(storeKey)
	bz := store.Get(types.KeyPrefixParams)
	cdc.MustUnmarshal(bz, &params)

	zeroInt := sdkmath.ZeroInt()
	params.ChainConfig.OsakaTime = &zeroInt
	if err := params.Validate(); err != nil {
		return err
	}
	bz = cdc.MustMarshal(&params)
	store.Set(types.KeyPrefixParams, bz)
	return nil
```

**File:** x/evm/keeper/abci.go (L24-44)
```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
	k.WithChainID(ctx)

	// cache parameters that's common for the whole block.
	cfg, err := k.EVMBlockConfig(ctx, k.ChainID())
	if err != nil {
		return err
	}
	k.SetHeaderHash(ctx)
	headerHashNum, err := ethermint.SafeInt64(cfg.Params.GetHeaderHashNum())
	if err != nil {
		panic(err)
	}
	if i := ctx.BlockHeight() - headerHashNum; i > 0 {
		h, err := ethermint.SafeUint64(i)
		if err != nil {
			panic(err)
		}
		k.DeleteHeaderHash(ctx, h)
	}
	return nil
```

**File:** x/evm/keeper/keeper.go (L348-370)
```go
// SetHeaderHash stores the hash of the current block header in the store.
func (k Keeper) SetHeaderHash(ctx sdk.Context) {
	acct := k.GetAccount(ctx, ethparams.HistoryStorageAddress)
	if acct != nil && acct.IsContract() {
		window := types.DefaultHistoryServeWindow
		params := k.GetParams(ctx)
		if params.HistoryServeWindow > 0 {
			window = params.HistoryServeWindow
		}
		// set current block hash in the contract storage, compatible with EIP-2935
		ringIndex := uint64(ctx.BlockHeight()) % window //nolint:gosec // G115 // won't exceed uint64
		var key common.Hash
		binary.BigEndian.PutUint64(key[24:], ringIndex)
		k.SetState(ctx, ethparams.HistoryStorageAddress, key, ctx.HeaderHash())
	} else {
		// fallback old implementation
		store := ctx.KVStore(k.storeKey)
		height, err := ethermint.SafeUint64(ctx.BlockHeight())
		if err != nil {
			panic(err)
		}
		store.Set(types.GetHeaderHashKey(height), ctx.HeaderHash())
	}
```

**File:** x/evm/keeper/state_transition.go (L97-143)
```go
func (k Keeper) GetHashFn(ctx sdk.Context, headerHashNum uint64) vm.GetHashFunc {
	return func(num64 uint64) common.Hash {
		h, err := ethermint.SafeInt64(num64)
		if err != nil {
			return common.Hash{}
		}
		upper, err := ethermint.SafeUint64(ctx.BlockHeight())
		if err != nil {
			return common.Hash{}
		}
		if upper == num64 {
			headerHash := ctx.HeaderHash()
			if len(headerHash) > 0 {
				return common.BytesToHash(headerHash)
			}
		}
		// Align check with https://github.com/ethereum/go-ethereum/blob/release/1.11/core/vm/instructions.go#L433
		var lower uint64
		if upper <= headerHashNum {
			lower = 0
		} else {
			lower = upper - headerHashNum
		}

		if upper > num64 {
			// The requested height is historical, query EIP-2935 contract storage
			headerHash := k.GetHeaderHash(ctx, num64)
			if headerHash.Cmp(common.Hash{}) != 0 {
				return headerHash
			} else if num64 >= lower {
				// Pre upgrade case
				// In case EIP-2935 is not supported and data cannot be found, we fetch historical info
				histInfo, err := k.stakingKeeper.GetHistoricalInfo(ctx, h)
				if err != nil {
					k.Logger(ctx).Debug("historical info not found", "height", h, "err", err.Error())
					return common.Hash{}
				}
				header, err := cmttypes.HeaderFromProto(&histInfo.Header)
				if err != nil {
					k.Logger(ctx).Error("failed to cast tendermint header from proto", "error", err)
					return common.Hash{}
				}
				return common.BytesToHash(header.Hash())
			}
		}
		return common.Hash{}
	}
```
