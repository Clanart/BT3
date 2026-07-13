### Title
Migration v6 `ToParams()` Drops `HeaderHashNum`, Causing BLOCKHASH to Return Zero for All Post-Migration Blocks — (`x/evm/migrations/v6/migrate.go`, `x/evm/migrations/v4/types/params.go`)

---

### Summary

The v6 migration calls `V4Params.ToParams()` which does not copy `HeaderHashNum` into the new `types.Params`. The field defaults to `0`. `Validate()` accepts `0` (only checks int64 overflow). `BeginBlock` then uses `headerHashNum = 0` to immediately delete every hash it just stored, leaving the EIP-2935 store permanently empty. `GetHashFn` consequently returns `common.Hash{}` (all-zeros) for every historical block. Any contract using `BLOCKHASH` for randomness or commit-reveal security is trivially exploitable post-upgrade.

---

### Finding Description

**Step 1 — Migration drops `HeaderHashNum`.**

`V4Params.ToParams()` constructs the new `types.Params` without setting `HeaderHashNum`: [1](#0-0) 

The field is absent, so Go zero-initialises it to `0`. `MigrateStore` calls `newParams.Validate()` before writing, but `Validate()` only calls `ValidateInt64Overflow(p.HeaderHashNum)`, which accepts `0` without complaint: [2](#0-1) [3](#0-2) 

The default is `256`: [4](#0-3) 

but `DefaultParams()` is never consulted during migration.

**Step 2 — `BeginBlock` self-destructs every stored hash.**

With `headerHashNum = 0`:

```
i = ctx.BlockHeight() - 0  →  i = ctx.BlockHeight()  →  i > 0  →  true
DeleteHeaderHash(ctx, ctx.BlockHeight())
```

`SetHeaderHash` stores the current block's hash, then `DeleteHeaderHash` immediately removes it at the same height: [5](#0-4) 

Net result: the EIP-2935 header-hash store is always empty post-migration.

**Step 3 — `GetHashFn` returns zero for all historical blocks.**

With `headerHashNum = 0`, `lower = upper - 0 = upper`. For any historical query `num64 < upper`:

1. `k.GetHeaderHash(ctx, num64)` → empty (store is empty).
2. Fallback guard: `num64 >= lower` → `num64 >= upper` → **false** for every historical block.
3. Returns `common.Hash{}`. [6](#0-5) 

The staking `GetHistoricalInfo` fallback is also unreachable because the `lower` bound equals `upper`.

---

### Impact Explanation

Every `BLOCKHASH(n)` call for `n < currentBlock` returns `0x000…000` after the upgrade. Any contract that uses `blockhash` as a randomness source or commit-reveal nonce (lottery, NFT mint, on-chain game, etc.) becomes trivially exploitable: the attacker simply submits the answer `0` and wins every time, draining contract-held EVM-denom funds through normal transaction execution.

---

### Likelihood Explanation

The v6 migration runs automatically on every chain that upgrades. No special privileges are required post-upgrade; the attacker only needs to submit a standard EVM transaction. The window is open from the upgrade block onward until governance corrects `HeaderHashNum` via `MsgUpdateParams`. Any chain running deployed contracts that use `BLOCKHASH` is immediately at risk.

---

### Recommendation

In `MigrateStore` (v6), explicitly set `HeaderHashNum` to the default after calling `ToParams()`:

```go
newParams = params.ToParams()
if newParams.HeaderHashNum == 0 {
    newParams.HeaderHashNum = types.DefaultHeaderHashNum
}
```

Additionally, add a minimum-value check in `Validate()` (e.g., `HeaderHashNum >= 1`) so that a zero value is rejected at the parameter level.

---

### Proof of Concept

1. Deploy a lottery contract that pays out to whoever submits `blockhash(block.number - 1)` as a guess.
2. Run the v6 migration (`MigrateStore`).
3. Observe `keeper.GetParams(ctx).HeaderHashNum == 0`.
4. In the next block, `BeginBlock` calls `SetHeaderHash` then `DeleteHeaderHash` at the same height — store remains empty.
5. Call `BLOCKHASH(block.number - 1)` from any contract — returns `0x000…000`.
6. Submit guess `0` to the lottery — wins every round, draining all contract funds. [7](#0-6) [8](#0-7)

### Citations

**File:** x/evm/migrations/v4/types/params.go (L11-39)
```go
func (p V4Params) ToParams() currenttypes.Params {
	chainConfig := currenttypes.ChainConfig{
		HomesteadBlock:      p.ChainConfig.HomesteadBlock,
		DAOForkBlock:        p.ChainConfig.DAOForkBlock,
		DAOForkSupport:      p.ChainConfig.DAOForkSupport,
		EIP150Block:         p.ChainConfig.EIP150Block,
		EIP150Hash:          p.ChainConfig.EIP150Hash,
		EIP155Block:         p.ChainConfig.EIP155Block,
		EIP158Block:         p.ChainConfig.EIP158Block,
		ByzantiumBlock:      p.ChainConfig.ByzantiumBlock,
		ConstantinopleBlock: p.ChainConfig.ConstantinopleBlock,
		PetersburgBlock:     p.ChainConfig.PetersburgBlock,
		IstanbulBlock:       p.ChainConfig.IstanbulBlock,
		MuirGlacierBlock:    p.ChainConfig.MuirGlacierBlock,
		BerlinBlock:         p.ChainConfig.BerlinBlock,
		LondonBlock:         p.ChainConfig.LondonBlock,
		ArrowGlacierBlock:   p.ChainConfig.ArrowGlacierBlock,
		GrayGlacierBlock:    p.ChainConfig.GrayGlacierBlock,
		MergeNetsplitBlock:  p.ChainConfig.MergeNetsplitBlock,
	}
	return currenttypes.Params{
		EvmDenom:            p.EvmDenom,
		EnableCreate:        p.EnableCreate,
		EnableCall:          p.EnableCall,
		ExtraEIPs:           p.ExtraEIPs.EIPs,
		AllowUnprotectedTxs: p.AllowUnprotectedTxs,
		ChainConfig:         chainConfig,
	}
}
```

**File:** x/evm/types/params.go (L38-39)
```go
	// DefaultHeaderHashNum defines the default number of header hash to persist.
	DefaultHeaderHashNum = uint64(256)
```

**File:** x/evm/types/params.go (L93-95)
```go
	if err := ValidateInt64Overflow(p.HeaderHashNum); err != nil {
		return err
	}
```

**File:** x/evm/types/params.go (L152-161)
```go
func ValidateInt64Overflow(i interface{}) error {
	num, ok := i.(uint64)
	if !ok {
		return fmt.Errorf("invalid parameter type: %T", i)
	}
	if num > math.MaxInt64 {
		return fmt.Errorf("value too large: %d, maximum value is: %d", num, uint64(math.MaxInt64))
	}
	return nil
}
```

**File:** x/evm/keeper/abci.go (L32-43)
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
```

**File:** x/evm/keeper/state_transition.go (L113-142)
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
```

**File:** x/evm/migrations/v6/migrate.go (L27-34)
```go
	newParams = params.ToParams()
	shanghaiTime := sdkmath.ZeroInt()
	newParams.ChainConfig.ShanghaiTime = &shanghaiTime
	if err := newParams.Validate(); err != nil {
		return err
	}
	bz = cdc.MustMarshal(&newParams)
	store.Set(types.KeyPrefixParams, bz)
```
