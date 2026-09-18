Confirmed: `rewards` (the `RewardsMethod` case) at `precompiles/distribution/distribution.go:204-205` calls `p.rewards(ctx, method, args)` directly, unlike every other view method in the same `Execute` switch (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`), all of which pass `value` into `p.validateInput(value, args, n)` → `pcommon.ValidateNonPayable(value)`. The `rewards` function itself, at `precompiles/distribution/distribution.go:543-579`, never receives or checks `value` at all, so an EVM call to `rewards(address)` that also attaches `msg.value` (native `usei`/`wei`) succeeds and returns data normally — the attached value is simply absorbed into the precompile's/contract's balance with no accounting, no event, and no way to recover it, since there is no withdraw path for stray value sent to precompile calls.

### Title
Distribution precompile `rewards()` view method omits the non-payable value check, permanently stranding any ETH/usei sent with the call - (File: `precompiles/distribution/distribution.go`)

### Summary
The `rewards` method of the distribution precompile (`0x0000000000000000000000000000000000001007`) is the only query/view method in `PrecompileExecutor.Execute` that does not call `pcommon.ValidateNonPayable(value)`. Every other view method (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) explicitly rejects non-zero `value` via `p.validateInput`. `rewards` skips this check entirely, so a value-bearing call to it succeeds.

### Finding Description
In `precompiles/distribution/distribution.go`, `Execute` dispatches `RewardsMethod` directly to `p.rewards(ctx, method, args)` (line 205) without passing `value`, in contrast to sibling views such as `case ParamsMethod: return p.params(ctx, method, args, value)` which forward `value` for validation. The `rewards` function (`precompiles/distribution/distribution.go:543-579`) has no parameter for `value` and performs no non-payable check whatsoever — it only validates argument length and looks up the delegator address before querying `DelegationTotalRewards`. Consequently, a caller can invoke `rewards(address)` on the precompile through a normal EVM `CALL` with `msg.value > 0`; the value transfer portion of that call is processed by the standard EVM value-transfer mechanics (crediting the precompile's/contract's own balance) while the precompile logic itself neither reverts nor records/refunds the attached value, since it was designed as a pure read-only query.

### Impact Explanation
Any usei/wei value attached to a `rewards()` call on the distribution precompile is silently absorbed with no on-chain accounting and no mechanism in the precompile (or any contract) to reclaim it — a direct, permanent loss of funds for the caller, matching the "permanent freezing/loss of funds" class from the reported bug. This is reachable by any ordinary EVM transaction sender or any contract calling the precompile, requiring no special privilege.

### Likelihood Explanation
Likelihood is low-to-moderate: exploitation requires a caller to mistakenly (or naively) attach `value` to what should be a pure `view`-style call, which is unusual but plausible for integrators unaware that Solidity interface annotations for precompile views are not always enforced identically to other non-payable methods on-chain — especially given every sibling method in this same precompile does enforce it, creating an inconsistent trust assumption for callers/tooling that assume uniform non-payable enforcement across all "read" methods of the precompile.

### Recommendation
Add the same `pcommon.ValidateNonPayable(value)` check to the `rewards` method (and its call site in `Execute`) that all other view methods in `precompiles/distribution/distribution.go` already perform, so that any value-bearing call to `rewards` reverts instead of stranding funds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Proof of Concept
1. Associate an EVM address to a Sei account and delegate to a validator so it has a non-zero rewards entry (any address works even with zero rewards, since the call itself doesn't require rewards > 0).
2. Call the distribution precompile at `0x0000000000000000000000000000000000001007` with function selector for `rewards(address)` and attach `value: ethers.parseEther("1")` (or any non-zero usei/wei amount) in the transaction.
3. Observe the call succeeds (`status: 1`) and returns the rewards data exactly as a zero-value call would — confirmed by the existing test file's own comment: "Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds), so this spec deliberately has no 'view rejects value' test." [5](#0-4) 
4. The `value` sent is now stuck at the precompile address with no `withdraw`/`refund` function exposed anywhere in `precompiles/distribution/distribution.go`, permanently freezing the sender's funds — directly analogous to the reported `SwapRouter.sol` `receive()` issue where ETH sent in has no corresponding withdrawal path.

### Citations

**File:** precompiles/distribution/distribution.go (L204-205)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
```

**File:** precompiles/distribution/distribution.go (L206-221)
```go
	case ParamsMethod:
		return p.params(ctx, method, args, value)
	case ValidatorOutstandingRewardsMethod:
		return p.validatorOutstandingRewards(ctx, method, args, value)
	case ValidatorCommissionMethod:
		return p.validatorCommission(ctx, method, args, value)
	case ValidatorSlashesMethod:
		return p.validatorSlashes(ctx, method, args, value)
	case DelegationRewardsMethod:
		return p.delegationRewards(ctx, method, args, value)
	case DelegatorValidatorsMethod:
		return p.delegatorValidators(ctx, method, args, value)
	case DelegatorWithdrawAddressMethod:
		return p.delegatorWithdrawAddress(ctx, method, args, value)
	case CommunityPoolMethod:
		return p.communityPool(ctx, method, args, value)
```

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

**File:** precompiles/distribution/distribution.go (L543-556)
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
```

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```
