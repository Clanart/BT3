### Title
`distribution` precompile's `rewards()` view accepts `msg.value` with no non-payable check, stranding sent wei - ([File: precompiles/distribution/distribution.go])

### Summary
The `rewards()` method on the Sei EVM `distribution` precompile (address `0x...1007`) is dispatched without calling `pcommon.ValidateNonPayable(value)`, unlike essentially every other query method on that same precompile (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool` all take `value` and are checked, and all state-changing methods reject non-zero value explicitly). This is analogous to the Convergence `SdtBuffer` bug: an amount/value is accepted into a code path whose logic never accounts for or forwards it, so it becomes permanently unreachable to the caller.

### Finding Description
In `Execute`, `RewardsMethod` is the only branch that calls `p.rewards(ctx, method, args)` directly, omitting the `value` argument and never validating it is zero: [1](#0-0) 

Compare with the sibling state-changing method that does check non-payability: [2](#0-1) 

And the sibling query methods, which are called with `value` and route through validation inside their own bodies (e.g. `delegationRewards` calls `p.validateInput(value, args, 2)`): [3](#0-2) 

`rewards()` itself never touches `value` at all — it only queries `DelegationTotalRewards` and packs the response: [4](#0-3) 

This same missing check is present identically across the maintained precompile version and every legacy version (`v580`, `v605`, `v606`, `v610`, `v614`, `v620`, `v630`, `v640`, `v65`, `v66`, `v67`), confirming it is a structural omission rather than an isolated typo: [5](#0-4) 

The project's own integration-test documentation explicitly flags this as a known quirk, corroborating that a value-bearing call to `rewards()` succeeds and the value is not routed anywhere by the precompile logic: [6](#0-5) [7](#0-6) 

Because Go-EVM's `Call` semantics transfer `msg.value` from the caller to the precompile's address (`0x...1007`) as part of dispatching the call before `Run`/`Execute` is invoked, and the `rewards()` handler never sends, credits, or refunds that value to any account (it only issues a read-only gRPC-style query and packs the response), the usei/wei that was moved to the precompile's account balance is not associated with any withdrawable owner in the distribution module's accounting, nor is it returned to the caller.

### Impact Explanation
Funds sent as `msg.value` alongside a `rewards(address)` call become stuck at the precompile's address. The precompile address is not a normal account with a private key or withdrawal path exposed through this or any other interface, and the distribution module's own ledger (`ValidatorOutstandingRewards`, `FeePool`, etc.) is not touched by this call, so the sent value is not recoverable via any withdrawal message. This is a permanent loss/freezing of funds for any caller (human, contract, or aggregator/router) that mistakenly or programmatically forwards `msg.value` on a call to this view method — which is easy to happen for callers that generically forward value on `call` to what looks like a plain external contract function, especially since Solidity view functions are conventionally `payable`-safe to call with zero value but this one silently accepts non-zero.

### Likelihood Explanation
Reachable by any unprivileged EVM caller or contract via a single transaction/call to the `distribution` precompile at `0x0000000000000000000000000000000000001007`, requiring no special privilege, association state, or governance action — only that the caller (often a proxy/router contract that blindly forwards `msg.value`) sends nonzero value with the `rewards(address)` selector. The bug is present in the current implementation and consistently reproduced across all legacy precompile versions, indicating it is a long-standing, unpatched behavior rather than a one-off regression.

### Recommendation
Add the same non-payable guard used by all sibling query/view methods to the `RewardsMethod` dispatch branch in `Execute`, e.g.:
```go
case RewardsMethod:
    if err = pcommon.ValidateNonPayable(value); err != nil {
        return nil, 0, err
    }
    return p.rewards(ctx, method, args)
```
Apply the same fix to every legacy version of the precompile (`v580`, `v605`, `v606`, `v610`, `v614`, `v620`, `v630`, `v640`, `v65`, `v66`, `v67`) if those code paths remain reachable/registered for older EVM contract versions, to guarantee any value sent with the call is rejected rather than silently absorbed and stranded.

### Proof of Concept
1. Deploy or use any contract, or call directly via `eth_sendTransaction`, targeting `0x0000000000000000000000000000000000001007` (`distribution` precompile).
2. Encode a call to `rewards(address delegator)` with a nonzero `value` (e.g. `msg.value = 1 ether`-equivalent in wei, aligned to the usei/wei split).
3. Observe the transaction succeeds (status `1`), returns the queried reward data, and the precompile's address balance increases by the sent `value`, while no bank transfer, refund, or state update credits that value to the caller, the `delegator`, or any distribution-module account.
4. Confirm no existing message (`WithdrawDelegatorReward`, `SetWithdrawAddress`, etc.) can recover value held at the precompile address, since the distribution module's accounting was never updated by this call — the wei is permanently unreachable.

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

**File:** precompiles/distribution/distribution.go (L543-578)
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
```

**File:** precompiles/distribution/distribution.go (L774-788)
```go
func (p PrecompileExecutor) delegationRewards(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()

	if err := p.validateInput(value, args, 2); err != nil {
		rerr = err
		return
	}

```

**File:** precompiles/distribution/legacy/v67/distribution.go (L204-207)
```go
		}
		return p.revokeWithdrawAuthorization(ctx, method, caller, args, value)
	case RewardsMethod:
		return p.rewards(ctx, method, args)
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

**File:** integration_test/precompile_tests/README.md (L76-80)
```markdown
  can only be exercised via `eth_call`/`staticCall`, never a real tx.
- **Guard tables differ per precompile** — e.g. json and pointerview accept
  DELEGATECALL, staking/gov/distribution/pointer reject it precompile-wide,
  and `distribution.rewards` accepts value (no non-payable check). Don't
  generalize dispatch tests; copy the per-method guards from the Go source.
```
