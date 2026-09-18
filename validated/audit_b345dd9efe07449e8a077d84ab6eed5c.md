Confirmed: `rewards()` in the distribution precompile is a query-type method that omits the `value` parameter entirely and never calls `pcommon.ValidateNonPayable` or `pcommon.HandlePaymentUsei`, unlike every other view function in the same file (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`), which all call `p.validateInput(value, args, N)` → `pcommon.ValidateNonPayable(value)`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
`rewards()` distribution precompile view accepts sent value without validation or refund, permanently stranding EVM-sent usei/wei - (File: precompiles/distribution/distribution.go)

### Summary
The `rewards` method of the distribution precompile (0x0000000000000000000000000000000000001007) is dispatched without any payability check. Every other query method in the same file explicitly rejects non-zero `value` via `p.validateInput` → `pcommon.ValidateNonPayable`, but `rewards` skips this validation entirely, matching the reported bug class where a function accepts more/any value than needed and never returns/uses the excess.

### Finding Description
In `Execute`, the switch statement calls `p.rewards(ctx, method, args)` for `RewardsMethod` without passing or checking `value`: [1](#0-0) . The `rewards` function signature itself does not even accept a `value` parameter, so it can never call `pcommon.ValidateNonPayable` [4](#0-3) . Contrast this with `params`, `validatorOutstandingRewards`, `communityPool`, etc., which all funnel through `validateInput`, which explicitly calls `pcommon.ValidateNonPayable(value)` before proceeding [3](#0-2) .

Because `rewards` is a precompile invoked via `StaticCall`/`Call` from the EVM, when it is called with a non-zero `value` through a regular `CALL` (not `STATICCALL`), the EVM will have already transferred the wei/usei balance from the caller to the precompile address as part of normal EVM value-transfer semantics before the precompile logic runs. Since `rewards()` never invokes `pcommon.HandlePaymentUsei` (which is the mechanism used elsewhere, e.g. in `precompiles/staking/legacy/*/staking.go` `delegate()`, to debit the precompile account and refund/redirect the sent coins back to the payer or into a state-changing operation) [5](#0-4) , any usei sent alongside a `rewards()` call is never refunded to the caller and is not consumed by any explicit accounting logic — it is simply left credited to the precompile's module/EVM address in the bank keeper. This directly mirrors the reported analog: `msg.value` accepted without a check/refund, resulting in permanent loss of the caller's funds. This is also explicitly called out as an intentional quirk in the integration test suite: "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)" [6](#0-5) .

### Impact Explanation
Any unprivileged EVM caller who invokes `rewards(address)` on the distribution precompile with a non-zero `value` will have that usei/wei amount transferred to the precompile's account and never refunded to them nor put toward any state-changing effect — a direct, permanent loss of user funds. This matches the "Accept only concrete fund loss" criterion. Because the precompile address is a fixed system address, the "lost" funds effectively become unspendable/stranded there, similar to sending tokens to a black hole address, unless a subsequent governance/operator action reclaims them (not available to the calling user).

### Likelihood Explanation
Likelihood is realistic but requires a user/integrator mistake: normally dApp/wallet integrations for a pure "view" call wouldn't attach `value`, but nothing in the ABI or on-chain enforcement prevents it, and this is exactly the pattern flagged as a known quirk by the project's own test suite comment, indicating the team is aware this is a live footgun for integrators building automated multicall/batch transaction flows where `value` might be non-zero unintentionally (e.g. copy-pasted call options from a payable method in the same interface).

### Recommendation
Add a `value *big.Int` parameter to `rewards()`, and validate it the same way as all other view methods via `pcommon.ValidateNonPayable(value)` (or route it through `p.validateInput(value, args, 1)` as done in `delegationRewards`/`validatorOutstandingRewards`), rejecting any call with non-zero value with an explicit error rather than silently accepting and stranding it.

### Proof of Concept
1. Caller (EVM address associated to a Sei account) invokes the distribution precompile at `0x0000000000000000000000000000000000001007` with method `rewards(address)` and a non-zero `msg.value` (e.g., 1 usei-equivalent wei), using a normal `CALL` opcode (not `STATICCALL`).
2. Standard EVM semantics debit the caller's balance and credit the precompile address with the sent value before precompile execution.
3. `Execute` dispatches to `p.rewards(ctx, method, args)`, which never inspects or validates `value` [1](#0-0) .
4. The call succeeds, returning the rewards data, but the transferred usei/wei is never returned to the caller nor consumed by `HandlePaymentUsei`, permanently stranding it at the precompile address.

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

**File:** precompiles/staking/legacy/v555/staking.go (L155-161)
```go
	if value == nil || value.Sign() == 0 {
		return nil, errors.New("set `value` field to non-zero to send delegate fund")
	}
	coin, err := pcommon.HandlePaymentUsei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), delegator, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, err
	}
```

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```
