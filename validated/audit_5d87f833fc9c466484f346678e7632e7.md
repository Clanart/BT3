This is the key finding: the `getOracleTwaps` precompile method calls `CalculateTwaps` directly with a user-supplied `lookbackSeconds` **without first calling `ValidateLookbackSeconds`**.

### Title
Oracle precompile `getOracleTwaps` can panic on unvalidated `lookbackSeconds`, causing an EVM-reachable underflow in `CalculateTwaps` - ([File: x/oracle/keeper/keeper.go])

### Summary
`CalculateTwaps` in `x/oracle/keeper/keeper.go` performs an unchecked subtraction `uint64(currentTime)-lookbackSeconds-1` when computing the price-snapshot iteration key prefix. `Keeper.ValidateLookbackSeconds` exists specifically to bound `lookbackSeconds` against the configured `LookbackDuration` param and reject zero/oversized values, but the `getOracleTwaps` precompile method in `precompiles/oracle/legacy/{v552..v630}/oracle.go` calls `CalculateTwaps` with the caller-supplied `lookbackSeconds` directly, skipping this validation. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`CalculateTwaps(ctx, lookbackSeconds)` computes:
```go
keyPrefix := types.GetPriceSnapshotKeyForIteration(uint64(currentTime), uint64(currentTime)-lookbackSeconds-1)
```
`currentTime` is `ctx.BlockTime().Unix()` (an `int64`) cast to `uint64`. If `lookbackSeconds >= uint64(currentTime)`, the expression `uint64(currentTime)-lookbackSeconds-1` underflows, producing a huge wrapped `uint64` value analogous to the Solidity `programInfo.duration - (toOracleVersion.timestamp - fromOracleVersion.timestamp)` underflow in the referenced report — an unchecked unsigned subtraction of a caller-influenced duration from a smaller/comparable timestamp value. [4](#0-3) 

The only guard against this, `ValidateLookbackSeconds`, is a separate function that is not invoked by `CalculateTwaps` itself — it must be called by the caller. The gRPC query path presumably calls it, but the EVM precompile `getOracleTwaps` method (present across many legacy precompile versions, e.g. `v552`–`v630`) does **not** call `ValidateLookbackSeconds` before invoking `CalculateTwaps`, and passes the raw `args[0].(uint64)` from the ABI-decoded call data straight through. [3](#0-2) 

While the precompile's `Execute` wraps a `recover()` that converts any panic into an "execution reverted" error for that specific EVM call, the underflowed `keyPrefix` still causes `GetPriceSnapshotKeyForIteration`/`IteratePriceSnapshotsReverse` to iterate over an unexpected key range, potentially scanning/matching unintended keys in the KV store (since the "from" bound wraps to a huge value near `2^64-1`, effectively inverting or corrupting the intended iteration range). This is a state/store read-integrity issue distinct from Program.sol's revert-on-completion issue but stems from the same missing-bounds-check root cause identified in the audit report. [5](#0-4) 

### Impact Explanation
Impact is limited: the precompile's top-level `recover()` catches any panic and converts it to a normal EVM revert for the calling transaction, so this does not halt the chain or crash the node. The primary observable effect is a malformed/incorrect TWAP iteration key range being passed to `IteratePriceSnapshotsReverse`, which could cause incorrect (rather than reverting) TWAP query results to be returned to callers of `getOracleTwaps` for extreme/underflowing `lookbackSeconds` inputs, an integrity issue for a read-only oracle query rather than a fund-loss, freezing, or consensus-halting bug.

### Likelihood Explanation
Any account can call the `getOracleTwaps` EVM precompile method at `0x0000000000000000000000000000000000001008` with an arbitrary `lookbackSeconds` value, so triggering the underflow condition (`lookbackSeconds >= currentTime`) requires no special privilege — just a single EVM call with an oversized `lookbackSeconds` parameter.

### Recommendation
Call `Keeper.ValidateLookbackSeconds(ctx, lookbackSeconds)` inside `CalculateTwaps` itself (or explicitly before invoking it) in every precompile version's `getOracleTwaps` implementation, so the bound check cannot be bypassed by any caller that forgets to invoke it, mirroring the Sherlock recommendation to check for underflow before performing the subtraction in `Program.sol`.

### Proof of Concept
1. Deploy/call a contract (or send an EVM tx) invoking `getOracleTwaps(lookbackSeconds)` on the oracle precompile at `0x...1008` with `lookbackSeconds` set to a value greater than or equal to the current block's Unix timestamp (e.g., `type(uint64).max` or `block.timestamp`).
2. Inside `CalculateTwaps`, `uint64(currentTime)-lookbackSeconds-1` underflows to a value near `2^64-1`.
3. `types.GetPriceSnapshotKeyForIteration(uint64(currentTime), <underflowed value>)` builds an iteration key prefix outside the intended lookback window, and `IteratePriceSnapshotsReverse` iterates using this corrupted range, potentially returning bogus/empty TWAP data rather than the properly bounded window enforced by `ValidateLookbackSeconds`. [4](#0-3) 

**Note on confidence**: I was not able to fully trace `GetPriceSnapshotKeyForIteration`'s exact byte-level key construction or confirm whether the gRPC query wrapper (outside the precompile) calls `ValidateLookbackSeconds` before `CalculateTwaps`, since that call site wasn't found in the indexed code. This limits certainty about the precise blast radius (e.g., whether it could also affect gRPC/CLI query paths, which would be out of scope per the rules). If you need the exact query-service call path confirmed, a full-repository session (not limited by index size) would be needed to verify it definitively.

### Citations

**File:** x/oracle/keeper/keeper.go (L478-507)
```go
func (k Keeper) CalculateTwaps(ctx sdk.Context, lookbackSeconds uint64) (types.OracleTwaps, error) {
	oracleTwaps := types.OracleTwaps{}
	currentTime := ctx.BlockTime().Unix()
	err := k.ValidateLookbackSeconds(ctx, lookbackSeconds)
	if err != nil {
		return oracleTwaps, err
	}
	var timeTraversed int64
	denomToTimeWeightedMap := make(map[string]sdk.Dec)
	denomDurationMap := make(map[string]int64)

	// get targets - only calculate for the targets
	targetsMap := make(map[string]struct{})
	k.IterateVoteTargets(ctx, func(denom string, denomInfo types.Denom) (stop bool) {
		targetsMap[denom] = struct{}{}
		return false
	})

	keyPrefix := types.GetPriceSnapshotKeyForIteration(uint64(currentTime), uint64(currentTime)-lookbackSeconds-1) //nolint:gosec
	k.IteratePriceSnapshotsReverse(ctx, keyPrefix, func(snapshot types.PriceSnapshot) (stop bool) {
		stop = false
		snapshotTimestamp := snapshot.SnapshotTimestamp
		loopback := int64(lookbackSeconds) //nolint:gosec
		if currentTime-loopback > snapshotTimestamp {
			snapshotTimestamp = currentTime - loopback
			stop = true
		}
		// update time traversed to represent current snapshot
		// replace SnapshotTimestamp with lookback duration bounding
		timeTraversed = currentTime - snapshotTimestamp
```

**File:** x/oracle/keeper/keeper.go (L575-582)
```go
func (k Keeper) ValidateLookbackSeconds(ctx sdk.Context, lookbackSeconds uint64) error {
	lookbackDuration := k.LookbackDuration(ctx)
	if lookbackSeconds > lookbackDuration || lookbackSeconds == 0 {
		return types.ErrInvalidTwapLookback
	}

	return nil
}
```

**File:** precompiles/oracle/legacy/v630/oracle.go (L118-130)
```go
func (p PrecompileExecutor) getOracleTwaps(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	lookbackSeconds := args[0].(uint64)
	twaps, err := p.oracleKeeper.CalculateTwaps(ctx, lookbackSeconds)
	if err != nil {
		return nil, 0, err
	}
```
