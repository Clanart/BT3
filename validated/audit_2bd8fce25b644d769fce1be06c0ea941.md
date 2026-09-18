Confirmed: the `rewards` view method on the Distribution precompile is the sole query handler that never calls `pcommon.ValidateNonPayable(value)`. All other query methods (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) route through `p.validateInput`, which enforces non-payable. `rewards` skips straight to argument-length validation and never rejects `value`.

### Title
Distribution precompile `rewards()` view accepts msg.value and permanently strands the sent SEI - ([File: precompiles/distribution/distribution.go])

### Summary
The `rewards(address)` query on the Distribution precompile (`0x0000000000000000000000000000000000001007`) is declared `payable` in the ABI but its Go handler never validates that `value == 0`, unlike every other query method on the same precompile. A caller who sends native value with a call to `rewards()` will have that value irreversibly locked at the precompile's account with no code path that refunds or forwards it.

### Finding Description
`PrecompileExecutor.Execute` dispatches `RewardsMethod` directly to `p.rewards(ctx, method, args)` [1](#0-0) , without the `pcommon.ValidateNonPayable(value)` check that guards every sibling query. Compare to `params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, and `communityPool`, all of which call `p.validateInput(value, args, n)` which internally invokes `pcommon.ValidateNonPayable(value)` before proceeding [2](#0-1) .

`rewards` itself only checks argument length and then queries `DelegationTotalRewards`, packing the output — it never inspects or rejects `value` [3](#0-2) .

Because the EVM/precompile framework transfers `msg.value` (as SEI/wei) into the precompile's account balance as part of a value-bearing CALL before `Execute` even runs, any value sent alongside a `rewards()` call is debited from the caller and credited to the precompile address by the EVM's normal value-transfer semantics, but the precompile logic (unlike `sendNative`, `HandlePaymentUsei`/`HandlePaymentUseiWei`, or `ValidateNonPayable`) provides no path to move it back out. Other transaction/state-mutating handlers such as `setWithdrawAddress` and `withdrawValidatorCommission` proactively call `pcommon.ValidateNonPayable(value)` for the exact same reason: to reject value up-front rather than let it be silently absorbed [4](#0-3) . This is precisely the bug-class in the external report: a "distribution"-style module receives fee/value assets it has no mechanism to hand back out.

The associated integration test explicitly documents this as a known behavioral quirk rather than a validated invariant: "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds), so this spec deliberately has no 'view rejects value' test" [5](#0-4) .

### Impact Explanation
Any unprivileged EVM caller (contract or EOA) who calls `rewards(address)` on the distribution precompile with non-zero `value` permanently loses that SEI — it is credited to the precompile's account and there is no withdrawal/refund mechanism reachable through the precompile ABI to recover it. This is a direct, permanent loss of user funds triggered by a single ordinary transaction, satisfying the "concrete fund loss or permanent freezing" acceptance bar. Because `rewards` is a very commonly called read/staking-dashboard method, wallets or aggregator contracts that mistakenly attach value (e.g. due to a bug in calling code, or an EIP-7702/batch-call sponsor forwarding leftover `msg.value`) would silently burn user funds.

### Likelihood Explanation
Likelihood is moderate: it requires a caller to attach non-zero value to a call that is semantically a read-only query (unusual but not implausible, e.g., contracts that forward all `msg.value` in a batched call, or user error via a wallet UI that defaults to attaching value). The precompile's own ABI marks the function `payable`, which will not raise any client-side warning, making misuse easy to trigger unintentionally. Every other similarly-shaped query method already defends against this, indicating the omission is an oversight rather than intentional design.

### Recommendation
Add `if err := pcommon.ValidateNonPayable(value); err != nil { return nil, 0, err }` (or route through `p.validateInput`) at the start of `PrecompileExecutor.rewards`, consistent with every other query handler in `precompiles/distribution/distribution.go`, so that value-bearing calls to `rewards()` revert instead of stranding funds. The same audit should be applied to any legacy precompile versions (`precompiles/distribution/legacy/v6xx/distribution.go`) that expose the same `rewards` method without the non-payable guard.

### Proof of Concept
1. Associate an EVM address with a Sei address and ensure it has some delegation (not strictly required — `rewards` only reads state).
2. From that EVM account, call the Distribution precompile at `0x0000000000000000000000000000000000001007` with:
   - `data` = ABI-encoded `rewards(address)` selector and a valid delegator address argument
   - `value` = any non-zero wei amount (e.g., `1 ether`)
3. Observe the transaction succeeds (status = 1), returning the expected `Rewards` struct, and the caller's balance is reduced by the sent value while the precompile module account balance increases by the same amount.
4. Confirm there is no subsequent transaction, precompile method, or governance action that returns this value to the caller — repeat the call, or attempt any other distribution precompile method, and observe the value remains permanently held by the precompile account, unlike `withdrawValidatorCommission`/`setWithdrawAddress`, which reject such calls outright via `ValidateNonPayable`.

### Citations

**File:** precompiles/distribution/distribution.go (L176-183)
```go
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawValidatorCommission(ctx, method, caller, evm)
```

**File:** precompiles/distribution/distribution.go (L204-205)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
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
