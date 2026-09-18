## Analog Found

### Title
Distribution precompile's `rewards()` view accepts `msg.value` with no non-payable check, permanently stranding sent SEI - (File: `precompiles/distribution/distribution.go`)

### Summary
The `rewards` method of the Distribution precompile (address `0x0000000000000000000000000000000000001007`) is the only query/view method in that precompile that does not reject a non-zero `msg.value`. Every sibling view method funnels through `p.validateInput`, which calls `pcommon.ValidateNonPayable(value)` before doing any work. `rewards` skips this check entirely, so an EVM caller can send SEI value alongside a `rewards(address)` call and that value is accepted by the EVM as a balance transfer to the precompile address, with no mechanism anywhere in the codebase to reclaim it — directly matching the reported bug class (funds accepted by a contract/address with no corresponding withdrawal path).

### Finding Description
In `precompiles/distribution/distribution.go`, the `Execute` dispatcher routes to `p.rewards(ctx, method, args)` without a value check [1](#0-0) , whereas every other query method (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) is invoked with the `value` argument and internally calls `p.validateInput`, which enforces non-payability [2](#0-1) .

The `rewards` implementation itself only validates argument length and never inspects `value` at all: [3](#0-2) 

This gap is explicitly acknowledged in the integration test suite as a known quirk of the phase-2 view methods: "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)" [4](#0-3) .

Since Cosmos precompiles are plain addresses handled by the EVM's native CALL value-transfer mechanics (not a Solidity contract with a `receive()`/withdraw function), any usei/wei value sent to `0x1007` via `rewards()` is credited to that address's balance by the StateDB but there is no bank/precompile logic anywhere that sweeps or forwards it back out — unlike `sendNative` in the Bank precompile, which explicitly calls `pcommon.HandlePaymentUseiWei` to route value into a real transfer [5](#0-4) . `rewards()` has no such handling, so the value is simply left stranded on a non-EOA, non-contract system address that no user or governance action can withdraw from.

### Impact Explanation
Any unprivileged EVM transaction sender who calls `rewards(address)` on the Distribution precompile with a non-zero `msg.value` will have that SEI permanently and irrecoverably locked at the precompile's address. This is a direct, concrete loss of funds for the caller with no recovery path, satisfying the "concrete fund loss or permanent freezing" bar.

### Likelihood Explanation
Trivial to trigger: any address (associated or not, since `rewards` takes the delegator address as an explicit argument rather than deriving it from `caller`) can send an ordinary EVM transaction/call to the well-known precompile address `0x1007` with the `rewards(address)` selector and non-zero value. No special privileges, prior state, or governance action are required — a naive integrator (e.g., a contract that forwards `msg.value` generically to a precompile call) could easily lose funds this way, and the codebase's own test suite already documents that this call path "would succeed and strand the funds."

### Recommendation
Add a `pcommon.ValidateNonPayable(value)` check at the top of `rewards` (or route it through `p.validateInput` like all its sibling view methods) so that a non-zero `msg.value` causes the call to revert instead of silently stranding funds, consistent with the rest of the Distribution precompile's query methods.

### Proof of Concept
1. From any EOA (association with a Sei address is not required since the delegator is passed as `args[0]`), send an EVM transaction to `0x0000000000000000000000000000000000001007` with calldata `rewards(address)` (selector `RewardsMethod`) and `value > 0`.
2. Trace through `Execute`: `case RewardsMethod: return p.rewards(ctx, method, args)` — note `value` is not passed and never checked [1](#0-0) .
3. `p.rewards` only validates `args` length and proceeds to answer the query successfully [6](#0-5) , so the call returns success (unlike calling `params`, `communityPool`, etc. with value, which revert via `ValidateNonPayable`).
4. The EVM's native value-transfer semantics move the sent SEI to the precompile address's balance as part of the CALL; no code path in the repository (bank, distribution, or otherwise) ever debits/forwards balance held at `0x1007`, so the funds remain stuck there permanently.

### Citations

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

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```

**File:** precompiles/bank/bank.go (L280-287)
```go
	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, 0, err
	}
```
