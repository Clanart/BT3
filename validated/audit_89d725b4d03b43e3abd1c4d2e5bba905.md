Confirmed: `rewards` (`RewardsMethod`) at `precompiles/distribution/distribution.go:204` and `precompiles/distribution/distribution.go:543-579` never calls `pcommon.ValidateNonPayable(value)` or any payment handler like `HandlePaymentUsei`. Every other view/query method on this precompile (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) routes through `p.validateInput`, which calls `ValidateNonPayable` at `precompiles/distribution/distribution.go:437-447`. `rewards` skips `validateInput` entirely and goes straight to `pcommon.ValidateArgsLength`. [1](#0-0) [2](#0-1) 

This is already flagged in the test suite as a known quirk: the distribution spec explicitly says a value-bearing call to `rewards()` would succeed and "strand the funds," and deliberately omits a "view rejects value" test for it. [3](#0-2) 

Because `rewards` is registered as a non-transaction (view) method, `Execute` runs it on a **branched/cached** `ctx.CacheContext()` whose writes are discarded, but the `value` (usei sent as `msg.value` in the EVM call) is not a Cosmos-store write — it is EVM-native balance transferred by the EVM interpreter itself to the precompile's address before/while `Run` executes, per standard `CALL` semantics. Since `rewards` never invokes `HandlePaymentUsei`/`HandlePaymentUseiWei` (the function explicitly designed to "refund payer because the following precompile logic will debit the payments from payer's account"), the transferred usei is credited to the distribution precompile's synthetic account and is never sent back to the caller or forwarded anywhere — it is permanently stranded, unlike the delegate/deposit/sendNative paths that correctly call `HandlePaymentUsei`/`HandlePaymentUseiWei` immediately. [4](#0-3) [5](#0-4) 

Unlike the original ether-fi report (where a Solidity `require` failing would revert the whole call and naturally return `msg.value`), this Sei case is different and arguably worse: the call **succeeds** (`rewards` returns valid data with no error), so there is no revert to unwind the EVM value transfer. The `Snapshot`/`RevertToSnapshot` mechanism in `x/evm/state/statedb.go` and `x/evm/state/state.go` only protects against failed calls; it does nothing here because the call doesn't fail — the usei is simply received and never accounted for, forwarded, or refunded. [6](#0-5) 

### Title
Distribution Precompile `rewards()` Accepts and Permanently Strands Attached Value - (File: precompiles/distribution/distribution.go)

### Summary
The `rewards` view method of the distribution precompile (`0x...1007`) is callable with a non-zero `msg.value` because it omits the `ValidateNonPayable` check that every other query method on the same precompile enforces via `validateInput`. Because it is also never routed through `HandlePaymentUsei`/`HandlePaymentUseiWei`, any usei sent along with the call is received by the precompile's underlying account and never returned to the caller.

### Finding Description
`Execute` dispatches `RewardsMethod` directly to `p.rewards(ctx, method, args)` without passing or checking `value`. `p.rewards` only validates argument length via `pcommon.ValidateArgsLength(args, 1)`; it never calls `pcommon.ValidateNonPayable(value)`. All sibling query methods (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) call `p.validateInput`, which enforces `ValidateNonPayable`. Because `rewards` is classified as a non-transaction method, it also runs on a `ctx.CacheContext()` whose Cosmos-side writes are discarded — but the EVM-level value transfer to the precompile address is not a Cosmos store write and is unaffected by this cache-context discard. The call completes successfully and returns a valid response, so there is no revert path that would otherwise unwind the value transfer via `RevertToSnapshot`.

### Impact Explanation
Any EVM caller (contract or EOA via `eth_call`/transaction) that attaches `msg.value` to a `rewards()` call permanently loses that usei with no compensating credit, refund, or bank-side movement — a direct, permanent fund loss reachable by any unprivileged EVM transaction sender through the public precompile interface. This matches "permanent freezing"/fund loss criteria.

### Likelihood Explanation
Likelihood depends on client tooling attaching value to a call that should be a pure view; well-formed dApp integrations typically use `staticcall`/`eth_call` for views and would not send value, but nothing in the ABI or contract enforces non-payability, and a mistaken or malicious integration (or wrapper contract that forwards `msg.value` indiscriminately) can trigger the loss with a single transaction.

### Recommendation
Add `if err := pcommon.ValidateNonPayable(value); err != nil { rerr = err; return }` at the top of `p.rewards` (mirroring `validateInput`'s behavior used by every sibling query), so the call reverts before the EVM transfers value to the precompile.

### Proof of Concept
1. Associate an EVM account with a Sei address, and have an existing delegation so rewards() has something to query.
2. Call `distribution.rewards(delegatorAddress)` with a positive `value` (e.g., `{value: 1e12}`), either directly or through a caller contract that forwards value.
3. Observe the call succeeds and returns delegation rewards data, and the caller's ether/usei balance is debited by `value` with no corresponding credit anywhere (no `HandlePaymentUsei` refund, no bank transfer using that coin) — the funds are stranded at the precompile's account.

### Citations

**File:** precompiles/distribution/distribution.go (L437-447)
```go
func (p PrecompileExecutor) validateInput(value *big.Int, args []interface{}, expectedArgsLength int) error {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return err
	}

	if err := pcommon.ValidateArgsLength(args, expectedArgsLength); err != nil {
		return err
	}

	return nil
}
```

**File:** precompiles/distribution/distribution.go (L543-579)
```go
func (p PrecompileExecutor) rewards(ctx sdk.Context, method *abi.Method, args []interface{}) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		rerr = err
		return
	}

	seiDelegatorAddress, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		rerr = err
		return
	}

	req := &distrtypes.QueryDelegationTotalRewardsRequest{
		DelegatorAddress: seiDelegatorAddress.String(),
	}

	wrappedC := sdk.WrapSDKContext(ctx)
	response, err := p.distrKeeper.DelegationTotalRewards(wrappedC, req)
	if err != nil {
		rerr = err
		return
	}

	rewardsOutput := getResponseOutput(response)
	ret, rerr = method.Outputs.Pack(rewardsOutput)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L1-12)
```typescript
/**
 * distribution precompile (0x…1007) — end-to-end semantics against a live Sei chain.
 *
 * Fixture: a pool account delegates via the staking precompile, then rewards
 * accrue per block. Reward amounts can never be asserted as exact equality
 * across blocks — the withdrawal test instead pins the bank-balance delta to
 * the amount decoded from the tx's own DelegationRewardsWithdrawn log.
 *
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
 */
```

**File:** precompiles/common/legacy/v606/precompiles.go (L80-101)
```go
	ctx = ctx.WithEventManager(sdk.NewEventManager())
	ctx = ctx.WithEVMPrecompileCalledFromDelegateCall(isFromDelegateCall)
	bz, err = p.executor.Execute(ctx, method, caller, callingContract, args, value, readOnly, evm, hooks)
	if err != nil {
		return bz, err
	}
	events := ctx.EventManager().Events()
	if len(events) > 0 {
		em.EmitEvents(ctx.EventManager().Events())
	}
	return bz, err
}

func HandlePrecompileError(err error, evm *vm.EVM, operation string) {
	if err != nil {
		state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
		metrics.IncrementErrorMetrics(operation, err)
	}
}

func (p Precompile) Prepare(evm *vm.EVM, input []byte) (sdk.Context, *abi.Method, []interface{}, error) {
	ctxer := state.GetDBImpl(evm.StateDB)
```

**File:** precompiles/staking/legacy/v640/staking.go (L210-228)
```go
func (p PrecompileExecutor) delegate(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, uint64, error) {
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	// if delegator is associated, then it must have Account set already
	// if delegator is not associated, then it can't delegate anyway (since
	// there is no good way to merge delegations if it becomes associated)
	delegator, associated := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !associated {
		return nil, 0, types.NewAssociationMissingErr(caller.Hex())
	}
	validatorBech32 := args[0].(string)
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send delegate fund")
	}
	coin, err := pcommon.HandlePaymentUsei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), delegator, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}
```

**File:** x/evm/state/state.go (L139-175)
```go
}

func (s *DBImpl) RevertToSnapshot(rev int) {
	// Add bounds checking
	if rev < 0 || rev >= len(s.snapshottedCtxs) {
		panic("invalid revision number")
	}

	s.ctx = s.snapshottedCtxs[rev]
	s.snapshottedCtxs = s.snapshottedCtxs[:rev]

	// The layer we just re-exposed becomes the writable top again, so it must no
	// longer be treated as a frozen (skippable) layer: writes may now land here,
	// and Snapshot() froze it when it was superseded. Unfreezing also drops the
	// skip on any store whose parent is this layer, so reads see the new writes.
	if f, ok := s.ctx.MultiStore().(interface{ Unfreeze() }); ok {
		f.Unfreeze()
	}

	// Find the watermark index to truncate the journal
	watermarkIndex := -1
	for i := len(s.journal) - 1; i >= 0; i-- {
		entry := s.journal[i]
		entry.revert(s)
		if wm, ok := entry.(*watermark); ok && wm.version == rev {
			watermarkIndex = i
			break
		}
	}

	// Truncate the journal to remove reverted entries. codeCache mutations are
	// journaled per address (codeCacheChange), so unrelated warmed entries survive.
	if watermarkIndex >= 0 {
		s.journal = s.journal[:watermarkIndex]
	}
}

```
