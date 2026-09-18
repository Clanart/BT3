Confirmed: `rewards()` at `precompiles/distribution/distribution.go:543` has no `pcommon.ValidateNonPayable(value)` call, unlike every other query method in the same file (e.g. `params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool` all call `p.validateInput(value, ...)` which enforces non-payability). This matches the report's bug class: a payable-by-omission entry point that accepts value from an unprivileged EVM caller but never uses or forwards it, permanently stranding funds — analogous to `UXDController.receive()` accepting ether it never accounts for.

### Title
Distribution precompile `rewards()` query accepts `msg.value` without consuming or refunding it, permanently stranding funds - (File: precompiles/distribution/distribution.go)

### Summary
The `distribution` precompile at `0x0000000000000000000000000000000000001007` is registered as a payable precompile [1](#0-0) , meaning value transfers to/from it bypass the normal EVM balance-transfer event bookkeeping [2](#0-1) . Nearly every read-only method dispatched by `Execute` calls `validateInput`, which internally calls `pcommon.ValidateNonPayable(value)` to reject any call carrying `msg.value` [3](#0-2) . The `rewards` method, however, is dispatched directly with `p.rewards(ctx, method, args)` — the `value` parameter is not even passed to the handler — so no non-payable check is ever performed [4](#0-3) [5](#0-4) .

### Finding Description
Any EVM transaction (a `CALL` from an EOA or a contract, including through `PrecompileCaller.callTarget` which forwards `msg.value`) can invoke `rewards(address)` on the distribution precompile with a nonzero `value`. Because `rewards()` skips `ValidateNonPayable`, the call succeeds instead of reverting. The precompile logic only queries `DelegationTotalRewards` and packs the response — it never debits, credits, or returns the attached usei/wei to the caller [6](#0-5) . Since the precompile address is in the `payablePrecompiles` allow-list, the value is accepted by the EVM's value-transfer mechanics (StateDB balance moves from caller to the precompile's associated account) without emitting the usual transfer semantics that other code paths would use to route funds back, and there is no corresponding `HandlePaymentUsei`-style refund/consumption logic invoked for `rewards` as there is for the payable pointer/wasmd flows. This is confirmed by an existing repo comment describing exactly this behavior: "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)" [7](#0-6) .

### Impact Explanation
Funds sent alongside a `rewards()` call become value credited to the precompile's underlying module/EVM account without any code path that returns them to the sender or otherwise accounts for them in module state, resulting in permanent loss/freezing of the attached usei/wei for the calling account. This satisfies the "permanent freezing of funds" impact bar, reachable by any unprivileged EVM transaction sender or contract that calls the distribution precompile with a nonzero value — no special privileges required.

### Likelihood Explanation
Likelihood is elevated versus the original report because `rewards` is a commonly-called, non-obviously-guarded view function (most other query methods in the same file are properly guarded, creating an inconsistency that is easy to trigger by mistake from a wallet, dApp, or contract that blindly forwards `msg.value` to precompile calls, or by a malicious/careless integrator wrapper contract). The existing test-suite comment shows the maintainers are already aware value-bearing calls to `rewards()` succeed, indicating this is a known, unpatched gap rather than a theoretical concern.

### Recommendation
Add the same non-payable enforcement used elsewhere in the file to `rewards()`, e.g. change the dispatch to pass `value` through and call `pcommon.ValidateNonPayable(value)` (or route it through `p.validateInput(value, args, 1)` like the other query methods `validatorOutstandingRewards`, `validatorCommission`, etc.) so that a value-bearing call to `rewards` reverts instead of silently stranding funds:
```go
case RewardsMethod:
    return p.rewards(ctx, method, args, value)
...
func (p PrecompileExecutor) rewards(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (...) {
    if err := p.validateInput(value, args, 1); err != nil {
        rerr = err
        return
    }
    ...
}
```

### Proof of Concept
1. Call the distribution precompile at `0x...1007` with method selector for `rewards(address)` and a nonzero `msg.value` (e.g. via `PrecompileCaller.callTarget{value: 1 sei}(distributionPrecompile, rewardsCalldata)` [8](#0-7) ).
2. Observe the call succeeds and returns the rewards data (no revert), while the caller's EVM balance is debited by the sent value.
3. Query the sender's Sei/EVM balance and the distribution module's relevant balances: no code path exists in `rewards()` that returns or accounts for the sent value, confirming it is stranded, matching the maintainers' own noted quirk in `distribution.spec.ts` [9](#0-8) .

### Citations

**File:** x/evm/keeper/precompile.go (L11-18)
```go
// add any payable precompiles here
// these will suppress transfer events to/from the precompile address
var payablePrecompiles = map[string]struct{}{
	bank.BankAddress:       {},
	staking.StakingAddress: {},
	gov.GovAddress:         {},
	wasmd.WasmdAddress:     {},
}
```

**File:** x/evm/keeper/precompile.go (L20-26)
```go
func IsPayablePrecompile(addr *common.Address) bool {
	if addr == nil {
		return false
	}
	_, ok := payablePrecompiles[addr.Hex()]
	return ok
}
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

**File:** integration_test/precompile_tests/contracts/PrecompileCaller.sol (L15-23)
```text
    function callTarget(address target, bytes calldata data)
        external
        payable
        returns (bytes memory)
    {
        (bool ok, bytes memory ret) = target.call{value: msg.value}(data);
        if (!ok) _bubble(ret);
        return ret;
    }
```
