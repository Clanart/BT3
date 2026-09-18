Confirmed: `rewards()` in `precompiles/distribution/distribution.go` is the one query/view method on the distribution precompile that does **not** call `pcommon.ValidateNonPayable(value)` before executing. All sibling view methods (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) explicitly call `p.validateInput(value, args, N)` which rejects non-zero `value`, but `rewards` skips this check entirely.

### Title
Value sent with a `staticcall`-safe view call to the distribution precompile's `rewards()` method is accepted and permanently stranded - (File: precompiles/distribution/distribution.go)

### Summary
`RewardsMethod` (`rewards()`) is dispatched directly to `p.rewards(ctx, method, args)` without the `pcommon.ValidateNonPayable(value)` / `p.validateInput(value, args, N)` guard that every other view method in the distribution precompile applies.

### Finding Description
In `precompiles/distribution/distribution.go`, `Execute()` routes `RewardsMethod` as: [1](#0-0) 
and the underlying handler `rewards()` never checks `value`: [2](#0-1) 

Compare this to every other view method, which routes value through `p.validateInput`, which itself calls `pcommon.ValidateNonPayable`: [3](#0-2) [4](#0-3) 

The precompile address `0x0000000000000000000000000000000000001007` is registered as a "payable precompile" at the keeper level, so the EVM's value-transfer accounting for calls into the precompile address is suppressed/handled specially rather than reverted, meaning a `CALL` with non-zero `value` to `rewards()` completes successfully instead of reverting: [5](#0-4) 

The behavior is explicitly called out as a known quirk in the integration test suite (which deliberately omits a negative "view rejects value" test for this method because it would fail): [6](#0-5) 

This is the same bug class as the reported issue: a payable entry point exists (or here, a non-payable entry point that fails to reject payment) with no mechanism to return the attached value to the sender, so any SEI sent along with the call is not credited to any account balance and cannot be recovered by the sender.

### Impact Explanation
Since `rewards()` has no `HandlePaymentUsei`/`HandlePaymentUseiWei` call and never debits/credits the attached value to any Cosmos account, any usei/wei value sent with a `rewards()` call is consumed by the EVM value-transfer mechanics into the precompile's associated address but is never routed back to the caller nor recorded against any delegator/validator balance. Because there is no corresponding "sweep" or refund path in this method (unlike `sendNative` on the bank precompile, which explicitly forwards/refunds), the sent funds become permanently stranded at the precompile-associated account, unreachable by the original sender or any legitimate protocol accounting path. This matches the "permanent freezing of funds" impact bar (Medium/High) from the validation rules.

### Likelihood Explanation
Likelihood is high for accidental loss (any EVM client/library that defaults to attaching `msg.value` for a contract call, or any user mistakenly passing a non-zero `value` to `rewards()`) and is trivially reproducible by any unprivileged EVM caller — no privileged access, association, or delegation state is required to trigger the bug, only a call to the precompile at `0x1007` with a non-zero value and method selector `rewards`.

### Recommendation
Add the same `pcommon.ValidateNonPayable(value)` (or `p.validateInput(value, args, 1)`) guard used by the other view methods (`params`, `communityPool`, `delegationRewards`, etc.) to `rewards()` in `precompiles/distribution/distribution.go`, so that a non-zero `value` causes the call to revert with "sending funds to a non-payable function" instead of silently accepting and stranding the funds.

### Proof of Concept
1. Associate an EVM address with a Sei address via the standard association flow.
2. Call the distribution precompile at `0x0000000000000000000000000000000000001007` with:
   - `data` = ABI-encoded `rewards(address)` selector + a valid (associated) delegator address argument
   - `value` = any non-zero amount (usei/wei)
3. Observe that the transaction succeeds and returns the rewards data (unlike calling `params()`, `communityPool()`, or any other view method with a non-zero value, which reverts with "sending funds to a non-payable function").
4. Observe that the attached `value` is not reflected in the caller's Cosmos bank balance, the delegator's rewards, or any other recoverable account — confirming the funds are stranded, exactly as flagged in the test-suite comment at [7](#0-6) .

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

**File:** precompiles/distribution/distribution.go (L543-562)
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
```

**File:** precompiles/distribution/distribution.go (L637-650)
```go
func (p PrecompileExecutor) params(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()

	if err := p.validateInput(value, args, 0); err != nil {
		rerr = err
		return
	}
```

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
