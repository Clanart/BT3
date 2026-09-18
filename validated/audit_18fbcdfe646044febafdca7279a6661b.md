### Title
Distribution precompile's `rewards()` view method omits the non-payable check, allowing sent value to be permanently stranded - ([File: precompiles/distribution/distribution.go])

### Summary
The `distribution` precompile at `0x0000000000000000000000000000000000001007` enforces a `ValidateNonPayable` (or equivalent `validateInput`, which wraps it) check on every one of its query/view methods except `rewards()`. `rewards()` is dispatched directly with no non-payable guard, so a caller can attach non-zero `msg.value` to a call to `rewards(address)` and the call will succeed rather than revert, exactly analogous to the reported `SchemaResolverUpgradeable.sol` issue where `attest`/`revoke` allowed unintended ETH transfers that could get stuck.

### Finding Description
In the `Execute` dispatcher, every other view method funnels through `p.validateInput(value, args, N)`, which calls `pcommon.ValidateNonPayable(value)` and rejects any non-zero value with `"sending funds to a non-payable function"`: [1](#0-0) 

This check is applied to `params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, and `communityPool`: [2](#0-1) [3](#0-2) 

However, `RewardsMethod` is dispatched straight to `p.rewards(ctx, method, args)`, which does not receive `value` at all and performs no non-payable validation: [4](#0-3) [5](#0-4) 

Because `rewards()` never calls `pcommon.ValidateNonPayable` or `pcommon.HandlePaymentUsei` (the latter is the mechanism other value-accepting precompile methods use to convert `value`/wei into an actual usei bank transfer, or refund it), any wei value attached to a `rewards(address)` call is neither rejected nor properly accounted for through the usei/wei StateDB bridge. This is confirmed by the project's own integration test suite, which explicitly documents this as a known quirk:

"Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds), so this spec deliberately has no 'view rejects value' test." [6](#0-5) 

This is directly analogous to the reported bug class: a function that is reachable with `value` attached but does not intend to (and cannot properly) process that value, resulting in funds becoming stuck at the precompile/contract address rather than being rejected or forwarded.

### Impact Explanation
Any unprivileged EVM user who calls `rewards(address)` on the distribution precompile with non-zero `msg.value` will have that value accepted into the EVM/StateDB balance associated with the precompile address (`0x...1007`) without a corresponding usei bank-side transfer being executed (unlike `delegate`/`HandlePaymentUsei`-based flows). Since the precompile address has no legitimate Sei bech32 counterpart that can spend/withdraw this stranded wei-side balance through normal precompile logic, and `rewards()` itself never sweeps or refunds it, the sent funds become effectively unrecoverable/stuck. This matches the "permanent freezing of funds" impact criterion — the loss is due to accidental (or attacker-induced, e.g., a poorly written integrating contract) misuse and results in permanent stranding rather than mere fee loss.

### Likelihood Explanation
Likelihood is moderate: this requires a user or an integrating smart contract to mistakenly send value on a call that is supposed to be a pure view/query (`rewards`), which is an easy mistake since the ABI does not explicitly forbid it and every sibling query method behaves consistently as non-payable except this one. Wallets, SDKs, or contracts that generically attach value or fail to zero it out when calling `rewards` would trigger this without any malicious intent required.

### Recommendation
Add `pcommon.ValidateNonPayable(value)` (or route through `p.validateInput`) at the top of `rewards()` before any other processing, matching every other view function in `precompiles/distribution/distribution.go`, so that value-bearing calls to `rewards` revert instead of silently stranding funds.

### Proof of Concept
1. Associate an EVM account with a Sei address and set up a validator delegation so a `DelegationTotalRewardsRequest` can be answered.
2. From the EVM account, call `distribution.rewards(delegatorAddress)` at `0x0000000000000000000000000000000000001007`, attaching non-zero `value` (e.g., `{value: ethers.parseEther("1")}`).
3. Observe that the call succeeds and returns the rewards data (rather than reverting with "sending funds to a non-payable function"), while the attached value is not routed back to the caller nor converted to a usei bank transfer — it is stranded, as documented directly in the test file: [7](#0-6) .

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

**File:** precompiles/distribution/distribution.go (L774-787)
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
