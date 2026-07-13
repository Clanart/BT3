### Title
Missing Initialization of `HeaderHashNum` and `HistoryServeWindow` in v8 Migration Corrupts `BLOCKHASH` Opcode Behavior - (`x/evm/migrations/v8/migrate.go`)

---

### Summary

The `x/evm` module v8 store migration only initializes `OsakaTime` in `ChainConfig` but fails to initialize two new `Params` fields — `HeaderHashNum` and `HistoryServeWindow` — that were added to the `types.Params` proto struct. After the upgrade, both fields are left at their proto3 zero value (`0`). A `HeaderHashNum` of `0` causes `BeginBlock` to immediately delete every block's header hash after setting it, and causes `GetHashFn` to return `common.Hash{}` (all-zeros) for every historical block, breaking the `BLOCKHASH` EVM opcode for all post-upgrade transactions.

---

### Finding Description

**New fields not initialized in migration**

`proto/ethermint/evm/v1/params.proto` defines two fields added after v7:

```
uint64 header_hash_num     = 7;   // default 256
uint64 history_serve_window = 8;  // default 8191
```

`DefaultParams()` sets them to their intended defaults:

```go
HeaderHashNum:      DefaultHeaderHashNum,      // 256
HistoryServeWindow: DefaultHistoryServeWindow,  // 8191
```

The v8 migration reads the v7-era serialized `Params` blob, which was written before these fields existed. Proto3 decoding leaves absent fields at `0`. The migration then only sets `OsakaTime` and writes the params back — `HeaderHashNum` and `HistoryServeWindow` remain `0`:

```go
// x/evm/migrations/v8/migrate.go
bz := store.Get(types.KeyPrefixParams)
cdc.MustUnmarshal(bz, &params)          // HeaderHashNum=0, HistoryServeWindow=0
zeroInt := sdkmath.ZeroInt()
params.ChainConfig.OsakaTime = &zeroInt  // only OsakaTime is set
// HeaderHashNum and HistoryServeWindow are never initialized
bz = cdc.MustMarshal(&params)
store.Set(types.KeyPrefixParams, bz)
```

`Params.Validate()` only checks `ValidateInt64Overflow` (overflow guard), not a minimum-value guard, so `0` passes validation silently.

**Effect 1 — `BeginBlock` deletes the current block's hash every block**

```go
// x/evm/keeper/abci.go
k.SetHeaderHash(ctx)                                    // stores hash for height H
headerHashNum, _ := ethermint.SafeInt64(cfg.Params.GetHeaderHashNum())  // = 0
if i := ctx.BlockHeight() - headerHashNum; i > 0 {     // i = H - 0 = H > 0 → always true
    h, _ := ethermint.SafeUint64(i)
    k.DeleteHeaderHash(ctx, h)                          // deletes hash for height H immediately
}
```

When `HeaderHashNum = 0`, `i = ctx.BlockHeight()`, so `DeleteHeaderHash` is called with the current block height — the same height that `SetHeaderHash` just stored. Every block's hash is erased from the KV store the moment it is written (on chains where the EIP-2935 history contract is not yet deployed).

**Effect 2 — `GetHashFn` returns `0x0` for every historical block**

```go
// x/evm/keeper/state_transition.go
var lower uint64
if upper <= headerHashNum {   // upper <= 0 → false for any real block height
    lower = 0
} else {
    lower = upper - headerHashNum  // lower = upper - 0 = upper
}

if upper > num64 {
    headerHash := k.GetHeaderHash(ctx, num64)  // returns 0x0 (deleted above)
    if headerHash.Cmp(common.Hash{}) != 0 {
        return headerHash
    } else if num64 >= lower {   // num64 >= upper → always false for historical blocks
        // staking fallback — never reached
    }
}
return common.Hash{}  // always returned for every historical block
```

With `lower = upper`, no historical `num64` satisfies `num64 >= lower`, so the staking-history fallback is never reached. The `BLOCKHASH` opcode returns `0x0` for every block.

---

### Impact Explanation

After the v7→v8 upgrade, the `BLOCKHASH` EVM opcode returns `0x0000...0000` for every queried block height. Any on-chain contract that uses `BLOCKHASH` for security-sensitive logic (commit-reveal randomness, lottery draws, time-lock verification, nonce derivation) will receive a predictable all-zero value. An attacker who knows this can:

- Pre-compute the "random" outcome of any `BLOCKHASH`-based lottery or randomness scheme and submit winning transactions with certainty.
- Bypass commit-reveal schemes that hash a user secret against a block hash, since the block hash is always `0`.
- Drain funds from any contract whose access control or payout logic depends on `BLOCKHASH`.

This is a deterministic EVM state-transition bug: all validators compute the same wrong value, so there is no consensus split, but every EVM execution that touches `BLOCKHASH` produces an incorrect result, enabling unauthorized fund extraction from affected contracts.

---

### Likelihood Explanation

The bug is triggered automatically on every chain that runs the `sdk54` (or equivalent) upgrade handler calling `RunMigrations`. No special attacker action is needed to activate it — the migration runs unconditionally. Any chain that:

1. Was initialized before `HeaderHashNum`/`HistoryServeWindow` were added to the proto, AND
2. Upgrades through the v8 migration path

will be affected. The window of exploitability is permanent (until a corrective governance parameter update is submitted), and any contract using `BLOCKHASH` is immediately vulnerable.

---

### Recommendation

In `MigrateStore` (v8), after unmarshalling the existing params, explicitly set the two new fields to their intended defaults before writing back:

```go
// x/evm/migrations/v8/migrate.go
cdc.MustUnmarshal(bz, &params)

zeroInt := sdkmath.ZeroInt()
params.ChainConfig.OsakaTime = &zeroInt

// Initialize new fields that did not exist in v7 state
if params.HeaderHashNum == 0 {
    params.HeaderHashNum = types.DefaultHeaderHashNum          // 256
}
if params.HistoryServeWindow == 0 {
    params.HistoryServeWindow = types.DefaultHistoryServeWindow // 8191
}
```

Additionally, add a minimum-value check in `ValidateInt64Overflow` (or a dedicated validator) so that `HeaderHashNum = 0` is rejected as an invalid parameter going forward.

---

### Proof of Concept

1. Chain is at consensus version 7 with params stored without `HeaderHashNum`/`HistoryServeWindow`.
2. Governance passes the `sdk54` upgrade; `RunMigrations` calls `Migrate7to8`.
3. `MigrateStore` reads the v7 params blob → `HeaderHashNum = 0`, `HistoryServeWindow = 0`.
4. Only `OsakaTime` is set; params are written back with `HeaderHashNum = 0`.
5. First `BeginBlock` after upgrade: `SetHeaderHash` stores hash for block H; `DeleteHeaderHash(ctx, H)` immediately removes it.
6. Any EVM transaction calling `BLOCKHASH(H-1)` → `GetHashFn` computes `lower = upper`, `num64 >= lower` is false, returns `0x0`.
7. Attacker deploys or calls an existing lottery contract that uses `BLOCKHASH` for randomness; knowing the result is always `0`, they win every draw and drain the contract balance.

**Key files:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** x/evm/migrations/v8/migrate.go (L17-30)
```go
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

**File:** x/evm/types/params.go (L38-68)
```go
	// DefaultHeaderHashNum defines the default number of header hash to persist.
	DefaultHeaderHashNum = uint64(256)
	// DefaultHistoryServeWindow DefaultHeaderHashNum defines the default number of hystorical value to serve for EIP2935.
	DefaultHistoryServeWindow = uint64(8191)
)

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

// DefaultParams returns default evm parameters
// ExtraEIPs is empty to prevent overriding the latest hard fork instruction set
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

**File:** x/evm/keeper/abci.go (L32-44)
```go
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

**File:** x/evm/keeper/state_transition.go (L113-143)
```go
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

**File:** proto/ethermint/evm/v1/params.proto (L25-29)
```text
  // header_hash_num is the number of header hash to persist.
  uint64 header_hash_num = 7;
  // historyServeWindow for EIP 2935
  uint64 history_serve_window = 8;

```
