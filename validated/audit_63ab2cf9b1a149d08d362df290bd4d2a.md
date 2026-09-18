This is a promising lead. Let me examine the `rewards()` function in the distribution precompile to confirm the missing non-payable check that could strand funds.Based on my investigation, the strongest reachable analog is the missing non-payable guard on the distribution precompile's `rewards()` view method, which lets a value-bearing call succeed while stranding the attached usei/wei permanently — the same "funds sent but not properly handled" failure class as the Aave push-payment report, just manifesting as silent fund loss rather than a failed redeem.

### Title
Missing non-payable check on `rewards()` view method permits value-bearing calls that permanently strand funds - (File: precompiles/distribution/distribution.go)

### Summary
The Aave report describes ETH becoming unredeemable because a push-payment (`transfer`) to a contract can revert or run out of gas. The reachable analog in sei-chain is the inverse failure mode of the same bug class: a Sei precompile method that is supposed to reject attached value (like Aave should have safely handled/rejected pushed ETH) instead **accepts** attached usei/wei on a call path that has no logic to return, credit, or otherwise account for it, permanently stranding the sender's funds inside the precompile's associated address.

### Finding Description
Sei's distribution precompile at `0x0000000000000000000000000000000000001007` exposes numerous "view" style methods (`rewards`, `params`, `validatorOutstandingRewards`, etc.) that read Cosmos SDK state and are not supposed to move funds. Nearly all of these guard against payable calls via `pcommon.ValidateNonPayable` (seen consistently across other precompiles, e.g. `precompiles/pointer/pointer.go` L99-100: `if err := pcommon.ValidateNonPayable(value); err != nil {`) [1](#0-0) . The generic helper `ValidateNonPayable` explicitly exists to reject "sending funds to a non-payable function" [2](#0-1) .

However, the integration test suite explicitly documents that `distribution.rewards()` is missing this guard:
"Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds), so this spec deliberately has no 'view rejects value' test." [3](#0-2) 

The `RewardsMethod` constant is defined in `precompiles/distribution/distribution.go` [4](#0-3) , and unlike a state-changing call such as `withdrawDelegationRewards` (which correctly forwards `value` into `HandlePaymentUseiWei`/refund logic per `precompiles/bank` patterns), a pure read/query method has no mechanism to refund or otherwise route the attached usei/wei to the caller. Since EVM `CALL` with nonzero `value` unconditionally debits the caller and credits the callee address inside go-ethereum's EVM before `Run` is even invoked, any wei sent with a call to `rewards()` is credited to the precompile's associated Sei address and, absent an explicit refund path (as `HandlePaymentUseiWei` provides for payable precompile methods, e.g. `precompiles/common/legacy/v630/precompiles.go` L229-246 [5](#0-4) ), that value is never returned to the sender.

### Impact Explanation
Any unprivileged EVM caller (EOA or contract) that attaches nonzero `value` to a call targeting `rewards()` on the distribution precompile permanently loses that usei/wei: the precompile has no logic to credit it back to the caller, and because it is a read-only/query method there is no downstream withdrawal flow that could later disburse it. This satisfies the "concrete fund loss or permanent freezing" bar — it is not merely informational, since real funds sent by any address that reaches this method (e.g., via wallets/dApps or scripted `eth_call`/`eth_sendTransaction` misuse) become irrecoverable by the sender.

### Likelihood Explanation
This is trivially reachable by any transaction sender or contract — a single EVM transaction with `to = 0x...1007` (distribution precompile), `data` encoding `rewards(...)`, and nonzero `value` is sufficient. No special privileges, precompile whitelisting, or unusual chain state are required. The precompile is a standard public-RPC-reachable address on every Sei EVM-enabled chain.

### Recommendation
Add a `pcommon.ValidateNonPayable(value)` check (matching the pattern used by every other non-state-mutating method across the precompile suite, e.g. `precompiles/pointer/pointer.go` L99-100) at the start of the `rewards()` handler in `precompiles/distribution/distribution.go`, so that value-bearing calls revert instead of silently stranding funds. Audit all other precompile "view" methods (across all versioned copies under `precompiles/*/legacy/*`) for the same missing guard to ensure consistency going forward.

### Proof of Concept
1. From any funded EVM account, encode a call to `rewards(address)` (or whichever selector matches the ABI-defined `RewardsMethod`) targeting the distribution precompile address `0x0000000000000000000000000000000000001007`.
2. Submit an EVM transaction with this data and `value` set to a nonzero amount (e.g., 1 usei worth of wei).
3. Observe the transaction succeeds (status 1) and returns the expected rewards data, exactly as the existing test suite comment predicts ("a value-bearing call would succeed and strand the funds").
4. Query the sender's post-call balance and the precompile's associated Sei address balance: the sent value is debited from the sender and credited to the precompile's Sei address, with no code path that refunds or forwards it — the funds are stranded.

### Citations

**File:** precompiles/pointer/pointer.go (L99-100)
```go
func (p PrecompileExecutor) AddNative(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
```

**File:** precompiles/common/legacy/v614/precompiles.go (L199-205)
```go
func ValidateNonPayable(value *big.Int) error {
	if value != nil && value.Sign() != 0 {
		return errors.New("sending funds to a non-payable function")
	}

	return nil
}
```

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```

**File:** precompiles/distribution/distribution.go (L34-34)
```go
	RewardsMethod                              = "rewards"
```

**File:** precompiles/common/legacy/v630/precompiles.go (L229-246)
```go
func HandlePaymentUseiWei(ctx sdk.Context, precompileAddr sdk.AccAddress, payer sdk.AccAddress, value *big.Int, bankKeeper putils.BankKeeper, evmKeeper putils.EVMKeeper, hooks *tracing.Hooks, depth int) (sdk.Int, sdk.Int, error) {
	usei, wei := state.SplitUseiWeiAmount(value)
	// refund payer because the following precompile logic will debit the payments from payer's account
	// this creates a new event manager to avoid surfacing these as cosmos events
	if err := bankKeeper.SendCoinsAndWei(ctx.WithEventManager(sdk.NewEventManager()), precompileAddr, payer, usei, wei); err != nil {
		return sdk.Int{}, sdk.Int{}, err
	}
	if hooks != nil {
		newCtx := ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))
		if hooks.OnEnter != nil {
			hooks.OnEnter(depth+1, byte(vm.CALL), evmKeeper.GetEVMAddressOrDefault(newCtx, precompileAddr), evmKeeper.GetEVMAddressOrDefault(newCtx, payer), []byte{}, GetRemainingGas(newCtx, evmKeeper), value)
		}
		if hooks.OnExit != nil {
			hooks.OnExit(depth+1, []byte{}, 0, nil, false)
		}
	}
	return usei, wei, nil
}
```
