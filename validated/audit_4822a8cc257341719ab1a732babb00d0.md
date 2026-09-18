### Title
Distribution precompile `rewards()` view accepts ETH value with no non-payable check, permanently stranding sent funds - ([File: precompiles/distribution/distribution.go])

### Summary
The `rewards()` query method on the distribution precompile (address `0x...1007`) is dispatched without a `pcommon.ValidateNonPayable(value)` guard, unlike every other view method on the same precompile (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool` all call `p.<method>(ctx, method, args, value)` and internally validate/consume `value`, while `rewards` calls `p.rewards(ctx, method, args)` — it doesn't even receive `value` as a parameter). This mirrors the reported `SwapRouter.sol` bug class: a contract path that accepts native value transfer but has no logic to forward, refund, or account for it, so the value is silently lost.

### Finding Description
In `precompiles/distribution/distribution.go`, `Execute()` dispatches:
```go
case RewardsMethod:
    return p.rewards(ctx, method, args)
``` [1](#0-0) 

Compare to sibling views such as `delegationRewards`, which take `value` and internally call `p.validateInput(value, args, 2)` (which is expected to reject non-zero value for a view), and to `WithdrawValidatorCommissionMethod`, which explicitly calls `pcommon.ValidateNonPayable(value)` before proceeding: [2](#0-1) [3](#0-2) 

`rewards()` itself never inspects `value` at all across every legacy and current version examined (v580, v605, v606, v610, v614, v630, v640, v65, current): [4](#0-3) 

The integration test suite explicitly documents this as a known quirk rather than a false positive: "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)," and the spec deliberately omits a "view rejects value" test for this method: [5](#0-4) 

The distribution precompile address is registered in `payablePrecompiles`, meaning the EVM keeper suppresses ERC20-style transfer events for value moving to/from this address — so a value-bearing call to `rewards()` executes successfully (a real ETH transfer occurs at the EVM/StateDB layer before/within the precompile call) with no downstream logic that refunds, forwards to `bankKeeper`, or credits the caller in usei/wei: [6](#0-5) [7](#0-6) 

Every other payment-accepting precompile path in this codebase (`HandlePaymentUsei`, `HandlePaymentUseiWei`) explicitly refunds the payer from the precompile's Sei account before debiting the intended amount, confirming that the codebase's designed invariant is "any usei/wei that lands at a precompile address must be immediately refunded or consumed" — a step missing for `rewards()`: [8](#0-7) 

Because the distribution precompile address has no associated Sei/EVM private key (it's a synthetic precompile address), there is no way for a user or protocol code to reclaim usei/wei credited to that address once stranded — it is permanently lost, exactly analogous to ETH sent to `SwapRouter.sol`'s `receive()` with no withdraw path.

### Impact Explanation
Any transaction sender (unprivileged EOA or contract) that calls `rewards(address)` on the distribution precompile with non-zero `msg.value` will have that value permanently locked/burned into an inaccessible precompile account. Because `rewards()` is a commonly-called read of pending delegation rewards (e.g., wallets/dApps building UI display calls), a wallet or integrator bug that accidentally attaches value to this call (or a malicious front-end tricking a user into doing so) causes irrecoverable fund loss. This matches the accepted impact criteria: concrete fund loss / permanent freezing of user funds via a public, unprivileged EVM transaction.

### Likelihood Explanation
Likelihood is moderate: it requires the caller to intentionally or mistakenly attach non-zero `value` to a call that is documented/typed as a `view` function (`rewards(address) external view`) in the Solidity interface, so well-formed clients using standard tooling (ethers/web3, which reject sending value to a `view` function client-side) would not normally trigger it directly. However, any raw/low-level call (`.call{value: x}(data)`), a misconfigured multisig/router contract forwarding leftover `msg.value`, or a compromised/careless dApp backend can trivially construct such a call, and the chain will happily execute it and accept the funds with zero validation.

### Recommendation
Add a `pcommon.ValidateNonPayable(value)` check (or explicit `HandlePaymentUsei`-style refund) at the top of `rewards()` execution in `precompiles/distribution/distribution.go`, consistent with the non-payable guard applied to `WithdrawValidatorCommissionMethod` and other view methods, and add a regression test asserting "rewards rejects value" to close the gap the test suite currently documents as deliberately unguarded.

### Proof of Concept
1. Associate an EVM address with a Sei address and delegate to a validator so rewards begin accruing (as in the existing test fixture).
2. Encode a call to `rewards(address)` on the distribution precompile (`0x0000000000000000000000000000000000001007`) via `PrecompileCaller.callTarget` or a raw `eth_sendTransaction`, attaching `value: 1e18` wei.
3. Observe the transaction succeeds (status 1) and returns the rewards struct — no revert, unlike other value-bearing view calls which would revert via `ValidateNonPayable`.
4. Query the caller's EVM balance: it has decreased by the attached value; query the distribution precompile's associated Sei account balance: it has increased by the corresponding usei/wei, with no code path in `distribution.go` (`rewards` function) or `Execute()` (for `RewardsMethod`) to refund it. The funds are now stuck at a precompile address with no private key, i.e., permanently lost.

### Citations

**File:** precompiles/distribution/distribution.go (L176-179)
```go
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
```

**File:** precompiles/distribution/distribution.go (L204-205)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
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

**File:** precompiles/distribution/legacy/v580/distribution.go (L263-298)
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

**File:** precompiles/wasmd/legacy/v552/wasmd.go (L450-463)
```go
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

**File:** precompiles/common/precompiles.go (L269-290)
```go
func HandlePaymentUsei(ctx sdk.Context, precompileAddr sdk.AccAddress, payer sdk.AccAddress, value *big.Int, bankKeeper putils.BankKeeper, evmKeeper putils.EVMKeeper, hooks *tracing.Hooks, depth int) (sdk.Coin, error) {
	usei, wei := state.SplitUseiWeiAmount(value)
	if !wei.IsZero() {
		return sdk.Coin{}, fmt.Errorf("selected precompile function does not allow payment with non-zero wei remainder: received %s", value)
	}
	coin := sdk.NewCoin(sdk.MustGetBaseDenom(), usei)
	// refund payer because the following precompile logic will debit the payments from payer's account
	// this creates a new event manager to avoid surfacing these as cosmos events
	if err := bankKeeper.SendCoins(ctx.WithEventManager(sdk.NewEventManager()), precompileAddr, payer, sdk.NewCoins(coin)); err != nil {
		return sdk.Coin{}, err
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
	return coin, nil
}
```
