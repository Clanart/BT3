### Title
Value-bearing calls to the `rewards()` view on the Distribution precompile permanently strand attached funds - ([File: precompiles/distribution/distribution.go])

### Summary
The Distribution precompile (`0x0000000000000000000000000000000000001007`) implements a `rewards()` query method that, unlike every other read-only method on the same precompile, is missing the `pcommon.ValidateNonPayable` guard that all other phase-2 view methods use to reject a non-zero `value`. This is functionally identical to the reported "ETH can get locked" bug class: a `payable`/value-accepting entry point that has no code path to use, forward, or refund the attached value, so any usei/wei sent along with the call becomes permanently unrecoverable.

### Finding Description
Sei's EVM precompiles validate that view/query methods are not called with attached value by calling `pcommon.ValidateNonPayable(value)` before dispatch, which returns an error (and reverts the whole EVM call, refunding the value) whenever `value.Sign() != 0`: [1](#0-0) 

This check is applied consistently across the phase-2 precompile query/view methods, but the integration-test spec for the distribution precompile documents explicitly that the `rewards()` view is the one exception: [2](#0-1) 

Because `rewards()` is a `STATICCALL`-safe/read-only query, its executor never touches or accounts for `msg.value` — there is no `HandlePaymentUsei`/refund call analogous to the one used by payable execute paths (e.g. the wasmd `execute()` precompile method, which explicitly refunds the payer when a coin/value mismatch occurs): [3](#0-2) 

Since `rewards()` skips the non-payable check that every sibling view method enforces, an EVM call (via a normal transaction or via a contract using `CALL` with `value`) that attaches usei/wei to a `rewards()` invocation succeeds instead of reverting. The precompile's `IsPayablePrecompile` allow-list determines whether transfer events are suppressed for known payable precompiles: [4](#0-3) 

but nothing in the `rewards()` execution path debits the payer back or forwards the value anywhere useful — the attached SEI value is simply absorbed with no corresponding logic to spend or return it, unlike genuine payable precompile methods (`execute`, `delegate`, etc.) which either consume the value for its stated purpose or explicitly refund it via `HandlePaymentUsei`.

### Impact Explanation
Any unprivileged EVM transaction sender or contract that calls `rewards()` on the Distribution precompile with non-zero `value` will have that value become permanently unrecoverable/stranded — no refund path exists for this specific view method, and it is not usable for any subsequent operation, making it equivalent to a burn of caller funds with no benefit to the protocol and no way for the funds to be reclaimed. This matches the "permanent freezing of funds" impact category referenced in the validation rules.

### Likelihood Explanation
Likelihood is low-to-moderate: this requires a caller (EOA, contract, or dApp integrator) to mistakenly or unnecessarily attach value to a query-only precompile call, which is not a normal interaction pattern (most SDKs/wallets do not attach value to `view` calls unless explicitly programmed to). However, unlike the reported analog in Panoptic (where the team asserted "no reason for end users to send ETH"), here the omission is inconsistent with the codebase's own established pattern of guarding *every other* view/query precompile method with `ValidateNonPayable`, indicating this is an accidental gap rather than an intentional design choice, and could be triggered by any composing contract or SDK that blindly forwards `msg.value` to whichever precompile method it calls (e.g. multicall-style batchers, in the same spirit as `Multicall.sol`/`BatchCallAndSponsor.sol` value forwarding in this repo): [5](#0-4) 

### Recommendation
Add the same `pcommon.ValidateNonPayable(value)` check to the `rewards()` query executor in `precompiles/distribution/distribution.go` (and any legacy versioned copies still reachable, e.g. `precompiles/distribution/legacy/v67/distribution.go`) that all other view methods already use, so that value-bearing calls to `rewards()` revert and refund the caller instead of silently accepting and stranding funds.

### Proof of Concept
1. Associate an EVM address with a Sei account and delegate to a validator via the staking precompile so rewards begin accruing.
2. Call `rewards(validatorAddr)` on the Distribution precompile at `0x0000000000000000000000000000000000001007` while attaching a non-zero `value` (e.g. `{ value: ethers.parseEther("1") }`), either directly or via a contract like `PrecompileCaller.callTarget` which forwards `msg.value`: [6](#0-5) 
3. Observe the call succeeds (unlike every other view method on payable/non-payable precompiles, which reverts with "sending funds to a non-payable function").
4. Observe the attached value is deducted from the caller's balance with no corresponding credit, refund, or usable effect anywhere in the protocol — the funds are permanently stranded, confirmed by the integration-test author's own comment that this quirk exists and is deliberately untested: [7](#0-6)

### Citations

**File:** precompiles/common/legacy/v640/precompiles.go (L198-204)
```go
func ValidateNonPayable(value *big.Int) error {
	if value != nil && value.Sign() != 0 {
		return errors.New("sending funds to a non-payable function")
	}

	return nil
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

**File:** precompiles/wasmd/legacy/v640/wasmd.go (L396-412)
```go
		return
	}

	useiAmt := coins.AmountOf(sdk.MustGetBaseDenom())
	if value != nil && !useiAmt.IsZero() {
		useiAmtAsWei := useiAmt.Mul(state.SdkUseiToSweiMultiplier).BigInt()
		coin, err := pcommon.HandlePaymentUsei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderAddr, useiAmtAsWei, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
		if err != nil {
			rerr = err
			return
		}
		// sanity check coin amounts match
		if !coin.Amount.Equal(useiAmt) {
			rerr = errors.New("mismatch between coins and payment value")
			return
		}
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

**File:** contracts/src/BatchCallAndSponsor.sol (L70-74)
```text
    function _executeCall(Call calldata callItem) internal {
        (bool success,) = callItem.to.call{value: callItem.value}(callItem.data);
        require(success, "Call reverted");
        emit CallExecuted(msg.sender, callItem.to, callItem.value, callItem.data);
    }
```

**File:** integration_test/precompile_tests/contracts/PrecompileCaller.sol (L14-23)
```text
contract PrecompileCaller {
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
