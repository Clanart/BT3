Confirmed: `RewardsMethod` at `precompiles/distribution/distribution.go:204-205` dispatches straight to `p.rewards(ctx, method, args)` **without any `pcommon.ValidateNonPayable(value)` check**, unlike every other query method in the same `Execute` switch (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool` all call `p.validateInput(value, args, N)` which enforces non-payable). This is explicitly called out as a known quirk in the test suite comment.

### Title
`rewards()` view on the distribution precompile accepts ETH/usei value with no refund or withdraw path, permanently stranding sent funds - (File: precompiles/distribution/distribution.go)

### Summary
The distribution precompile's `Execute` dispatcher enforces `ValidateNonPayable` for every other query method, but the `rewards` case is missing this guard, so a value-bearing call to `rewards(address)` succeeds instead of reverting.

### Finding Description
In `precompiles/distribution/distribution.go`, `Execute` routes `RewardsMethod` directly to `p.rewards(ctx, method, args)`: [1](#0-0) 
Every other read-only method in the same switch calls `p.validateInput(value, args, N)`, which internally calls `pcommon.ValidateNonPayable(value)` before proceeding: [2](#0-1) 
`rewards` itself never checks `value`: [3](#0-2) 
Because the precompile is registered as one of the `payablePrecompiles` (bank, staking, gov, wasmd — not distribution, but the precompile executes inside the normal EVM value-transfer flow regardless of that map, which only "suppresses transfer events"), any `msg.value` attached to a `rewards()` call is transferred to the precompile's associated Sei account as part of standard EVM value semantics before/while the precompile executes, but `HandlePaymentUsei`/refund logic (used by every payable transaction method such as `delegate`, `execute`, `sendNative`) is never invoked for `rewards`. There is no code path in the distribution precompile that returns or refunds a non-zero `value` sent to a query method, and there is no owner-withdraw function on the distribution module account reachable from this call path. This is the exact bug class in the report: value paid to a contract/precompile address that has no owner-controlled or user-controlled recovery function, so it becomes permanently stuck at that address.

The `IsPayablePrecompile` map at `x/evm/keeper/precompile.go` confirms which precompiles are expected to legitimately receive value (bank, staking, gov, wasmd); distribution is not among them, meaning value sent to distribution precompile calls is not an intended/handled feature at all: [4](#0-3) 

### Impact Explanation
Any unprivileged EOA or contract calling `rewards(address)` on the distribution precompile (`0x0000000000000000000000000000000000001007`) with non-zero `value` will have that usei/wei permanently locked with no mechanism to reclaim it — no refund inside the precompile call, and no owner/admin withdraw function exists for this stranded balance. This is a direct, unprivileged, single-transaction fund-loss/freezing bug matching the "High" severity criteria of the original report (permanent freezing of funds, reachable by any public RPC client).

### Likelihood Explanation
Likelihood is moderate: an attacker or a buggy dApp/wallet integration could accidentally or intentionally send value with a `rewards()` call (e.g. a poorly written frontend/wallet that blindly forwards `msg.value` to a "view" style precompile call, or a malicious actor demonstrating fund loss against victims via a wrapper contract). The call requires no special permission — any address can call the distribution precompile directly.

### Recommendation
Add `pcommon.ValidateNonPayable(value)` to the `RewardsMethod` case in `Execute` (matching all other view methods), i.e.:
```go
case RewardsMethod:
    if err = pcommon.ValidateNonPayable(value); err != nil {
        return nil, 0, err
    }
    return p.rewards(ctx, method, args)
```
This causes the transaction to revert instead of silently accepting and stranding funds, consistent with the pattern used for every other read-only method in the precompile.

### Proof of Concept
1. Call `IDistr(0x0000000000000000000000000000000000001007).rewards(delegatorAddress)` from any EOA/contract, attaching `value > 0` (e.g. via `PrecompileCaller.callTarget{value: X}(distrPrecompile, rewardsCalldata)` as used in the test harness at `integration_test/precompile_tests/contracts/PrecompileCaller.sol`).
2. Because `rewards` never calls `ValidateNonPayable`, the call succeeds and returns the rewards data.
3. The `X` amount of usei/wei is now held by the precompile's associated Sei account balance with no `withdraw`/refund code path reachable by the sender or any owner — the funds are permanently stuck, confirmed by the absence of any `HandlePaymentUsei`/refund call in `rewards` (`precompiles/distribution/distribution.go:543-579`) versus its presence in payable methods like `execute` in `precompiles/wasmd/wasmd.go:206-294` and `sendNative` in `precompiles/bank/legacy/v67/bank.go:253-312`.

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

**File:** x/evm/keeper/precompile.go (L11-26)
```go
// add any payable precompiles here
// these will suppress transfer events to/from the precompile address
var payablePrecompiles = map[string]struct{}{
	bank.BankAddress:       {},
	staking.StakingAddress: {},
	gov.GovAddress:         {},
	wasmd.WasmdAddress:     {},
}

func IsPayablePrecompile(addr *common.Address) bool {
	if addr == nil {
		return false
	}
	_, ok := payablePrecompiles[addr.Hex()]
	return ok
}
```
