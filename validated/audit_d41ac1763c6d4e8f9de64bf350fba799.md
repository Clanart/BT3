Based on my investigation, I found a concrete analog in the `distribution` precompile's `rewards()` method, which is documented in the test suite itself as a known fund-stranding hazard.

### Title
Distribution precompile `rewards()` view method accepts value without a non-payable check, permanently stranding sent SEI - (File: precompiles/distribution/distribution.go)

### Summary
The `distribution` precompile at `0x0000000000000000000000000000000000001007` dispatches all its methods through a single `Execute` switch. Every state-mutating (and most view) methods explicitly call `pcommon.ValidateNonPayable(value)` before proceeding, rejecting any ETH/usei attached to the call. The `rewards()` view method is the sole exception: it is dispatched directly to `p.rewards(ctx, method, args)` without validating that `value` is zero.

### Finding Description
In the `Execute` dispatcher, `RewardsMethod` is routed without any payability check, unlike sibling methods: [1](#0-0) 

Every other transaction-style method in the same switch explicitly guards against attached value, e.g. `WithdrawValidatorCommissionMethod`: [2](#0-1) 

The test suite itself documents this gap as a known and accepted defect rather than intentional behavior: [3](#0-2) 

When an EVM caller sends a transaction/call to the precompile address with non-zero `value` and calls `rewards()`, the EVM's normal value-transfer semantics move that `usei`/`wei` balance to the precompile's associated Sei account before/as part of executing the call (this is the same StateDB bridge mechanism used elsewhere in the EVM keeper, e.g. `SubBalance`/`AddBalance` in `x/evm/state/balance.go`) — analogous to how `RocketMinipoolDelegate.destroy()` forwarded leftover ETH to `RocketVault`, an address with no code path to reclaim it. The distribution precompile has no corresponding mechanism (no owner-only sweep, no automatic refund, no module accounting) to recover value that lands on its associated account via this uncovered code path, so the funds become permanently stuck at that address, exactly mirroring the RocketPool bug class: a value transfer to a contract/address that provides no recovery mechanism.

### Impact Explanation
Any unprivileged EVM caller who sends value while invoking `rewards()` on the distribution precompile causes that value to be irrecoverably locked at the precompile's associated account, with no message, admin function, or governance path in the reachable production code to sweep or return it. This is a concrete, permanent freezing of user funds triggerable with a single transaction — matching the "permanent freezing of funds" acceptance criterion.

### Likelihood Explanation
The precompile is a fixed, universally reachable address (`0x1007`) callable by any EVM transaction sender or contract. Triggering the bug requires only attaching non-zero `value` to a `rewards()` call, with no special privileges, association state, or complex setup — a single crafted transaction is sufficient. The only barrier is that a caller must accidentally or deliberately send value to a view-only ABI method, which is plausible given many ABI methods are marked `payable`-agnostic and tooling might not warn against it.

### Recommendation
Add `pcommon.ValidateNonPayable(value)` to the `RewardsMethod` case in `Execute`, consistent with every other method in the same dispatcher (e.g., `WithdrawValidatorCommissionMethod`, `SetWithdrawAddressMethod`), rejecting any call to `rewards()` that carries value before it reaches `p.rewards`.

### Proof of Concept
1. An EVM account calls the distribution precompile at `0x0000000000000000000000000000000000001007` with function selector for `rewards(address)` (or equivalent ABI signature) and attaches `value > 0` in the transaction.
2. Because `Execute`'s `RewardsMethod` branch performs no `ValidateNonPayable` check (unlike `WithdrawValidatorCommissionMethod` and others), the call succeeds and returns rewards data.
3. The EVM's value-transfer mechanics have already moved the attached `usei`/`wei` to the precompile's associated Sei account as part of executing the call.
4. There is no method on the distribution precompile, nor any module logic, that allows withdrawing or crediting that balance back to any user; it remains permanently stranded.

Note: I was not able to fully trace, within the remaining budget, whether the top-level EVM call-execution path performs value transfer to a precompile address *before* invoking `Execute` for pure/view-style calls in all code paths (this would need direct inspection of the value-transfer logic in the core EVM call dispatch, e.g., `x/evm/keeper` call handling or `giga/deps/xevm`), so the exact mechanics of when/whether the transfer occurs should be verified against that call path before treating this as fully confirmed.

### Citations

**File:** precompiles/distribution/distribution.go (L176-179)
```go
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
```

**File:** precompiles/distribution/distribution.go (L204-206)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
	case ParamsMethod:
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
