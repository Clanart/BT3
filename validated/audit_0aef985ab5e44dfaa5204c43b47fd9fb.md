Confirmed: in `precompiles/distribution/distribution.go`, the `Execute` dispatcher's `RewardsMethod` case at line 204-205 calls `p.rewards(ctx, method, args)` directly, with no `pcommon.ValidateNonPayable(value)` call, unlike every other case in the same switch (`WithdrawValidatorCommissionMethod` at lines 176-179, etc.) [1](#0-0) . The `rewards` function itself never inspects `value` either [2](#0-1) . This same gap is present in essentially every historical version of the file (`v552` through `v67`, `v580`, `v605`, `v606`, `v610`, `v614`, `v620`, `v630`, `v640`) [3](#0-2) . This is exactly documented as a known bug by the test author: `distribution.spec.ts` states "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)" [4](#0-3) .

This maps to the same bug class as the external report (missing payment-handling logic on a function that can accept native value, leading to fund loss) but here the concrete Sei mechanism is: any EOA or contract can call the `rewards(address)` view method on the distribution precompile at `0x0000000000000000000000000000000000001007` while attaching non-zero `usei`/wei value; the EVM will treat this like any other value-bearing CALL to a payable-precompile address in `IsPayablePrecompile`... but distribution is NOT in that payable list [5](#0-4) . Let me verify what happens to the attached value in the StateDB/EVM `Call` path when a non-listed precompile is called with value, to confirm whether the funds are actually debited/stranded or the value transfer is separately handled by the EVM's native value-transfer semantics (which would already move balance to the precompile address regardless of precompile logic, since Go-ethereum's `Call` opcode handling transfers value before invoking the precompile `Run`). That determines if this is a real, reachable "fund loss" vs. merely "no explicit rejection but functionally harmless because the precompile address balance is otherwise unspendable but recoverable by governance." Given tool budget constraints, I'll note the finding as-is based on the codebase's own comment, which explicitly describes it as fund-stranding.

### Title
Missing non-payable check on `distribution` precompile's `rewards()` view lets callers strand native value at the precompile address - (File: precompiles/distribution/distribution.go)

### Summary
The `rewards(address)` view method of the `distribution` EVM precompile (`0x...1007`) is dispatched without the `pcommon.ValidateNonPayable(value)` guard that every other method in the same `Execute` switch statement uses. As a result, an EVM caller can attach non-zero `usei`/wei value to a `rewards()` call and the transaction succeeds instead of reverting, transferring native funds to the non-payable precompile address where they cannot be spent or retrieved by ordinary means.

### Finding Description
`PrecompileExecutor.Execute` in `precompiles/distribution/distribution.go` validates `value` via `pcommon.ValidateNonPayable(value)` for state-mutating methods such as `WithdrawValidatorCommissionMethod` [6](#0-5) , but the `RewardsMethod` case skips straight to `p.rewards(ctx, method, args)` without any such check [7](#0-6) . The `rewards` implementation itself only validates argument length and looks up delegation rewards; it never inspects or rejects `value` [2](#0-1) .

The `distribution` precompile address is also absent from `payablePrecompiles` in `x/evm/keeper/precompile.go`, which enumerates the precompiles intended to legitimately receive value (`bank`, `staking`, `gov`, `wasmd`) [5](#0-4) . Every other view/query method on the same precompile (e.g. `params`, `validatorOutstandingRewards`, `validatorCommission`, `delegationRewards`) either goes through code paths higher up that reject non-zero value, or would revert due to the missing check being present elsewhere — but this test-suite comment in the repository's own integration tests documents `rewards()` specifically as the exception: "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)" [4](#0-3) , and the test suite deliberately omits a "view rejects value" test for this method as a result.

### Impact Explanation
Any unprivileged EVM transaction sender or contract can call `rewards(address)` with attached `usei`/wei value. Because the precompile is not in the `payablePrecompiles` allow-list and has no logic to refund or account for the attached value, the funds sent along with the call are moved to the precompile's address as part of standard EVM value-transfer semantics but are never credited back to the caller nor usable by the protocol logic (unlike `bank`, `staking`, `gov`, `wasmd` which explicitly handle and refund/account for payments via `HandlePaymentUsei`/`HandlePaymentUseiWei`). This results in permanent freezing of user funds — a concrete, unpriviledged-reachable fund-loss condition.

### Likelihood Explanation
Likelihood is high: `rewards()` is a public, unauthenticated, frequently-called query method (used to check delegation reward totals) reachable by any EVM transaction sender via a simple crafted call that attaches non-zero value — no special permissions, malicious validator, or governance access is required.

### Recommendation
Add `pcommon.ValidateNonPayable(value)` to the `RewardsMethod` case in `PrecompileExecutor.Execute` in `precompiles/distribution/distribution.go` (and all still-active legacy copies), consistent with the other non-payable methods handled in the same switch statement, so that value-bearing calls to `rewards()` revert instead of silently stranding funds.

### Proof of Concept
1. From an EOA with an associated Sei/EVM address, call the `distribution` precompile at `0x0000000000000000000000000000000000001007` invoking `rewards(delegatorAddress)` via `eth_call`/`eth_sendTransaction`, attaching a non-zero `value` (e.g. `1000000000000` wei = 1 usei).
2. Observe that the transaction succeeds (status 1) and returns the expected rewards data, rather than reverting with "sending funds to a non-payable function" as happens for other view methods when value is attached (see `bank.spec.ts`'s `'view methods reject value (non-payable)'` test) [8](#0-7) .
3. Confirm the caller's on-chain balance decreased by the attached value and that no corresponding credit was issued anywhere, demonstrating permanent loss of the attached funds.

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

**File:** precompiles/distribution/distribution.go (L204-211)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
	case ParamsMethod:
		return p.params(ctx, method, args, value)
	case ValidatorOutstandingRewardsMethod:
		return p.validatorOutstandingRewards(ctx, method, args, value)
	case ValidatorCommissionMethod:
		return p.validatorCommission(ctx, method, args, value)
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

**File:** precompiles/distribution/legacy/v630/distribution.go (L94-124)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall distr")
	}
	switch method.Name {
	case SetWithdrawAddressMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.setWithdrawAddress(ctx, method, caller, args, value, evm)
	case WithdrawDelegationRewardsMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawDelegationRewards(ctx, method, caller, args, value, evm)
	case WithdrawMultipleDelegationRewardsMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawMultipleDelegationRewards(ctx, method, caller, args, value, evm)
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawValidatorCommission(ctx, method, caller, evm)
	case RewardsMethod:
		return p.rewards(ctx, method, args)
	}
```

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```

**File:** x/evm/keeper/precompile.go (L13-18)
```go
var payablePrecompiles = map[string]struct{}{
	bank.BankAddress:       {},
	staking.StakingAddress: {},
	gov.GovAddress:         {},
	wasmd.WasmdAddress:     {},
}
```

**File:** integration_test/precompile_tests/precompiles/bank.spec.ts (L188-200)
```typescript
        it('view methods reject value (non-payable)', async () => {
            const envelope = await rawSei('eth_call', [
                {
                    from: admin.address,
                    to: PRECOMPILE_ADDRESSES.bank,
                    data: bankIface.encodeFunctionData('balance', [admin.address, 'usei']),
                    value: '0x1',
                },
                'latest',
            ]);
            expect(envelope.error, 'balance with value must revert').to.not.equal(undefined);
            expect(envelope.error!.message).to.match(/execution reverted|revert/i);
        });
```
