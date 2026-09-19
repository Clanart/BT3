### Title
Value sent to `rewards()` on the distribution precompile is silently accepted and permanently stranded - (File: `precompiles/distribution/distribution.go`)

### Summary
The `distribution` precompile at `0x0000000000000000000000000000000000001007` is not registered as a "payable precompile," has no function that can move funds out of its address, and yet its `rewards()` view method accepts `value`-bearing calls without rejecting them, unlike every other method in the same file. Any usei/wei sent along with a `rewards()` call is transferred to the precompile's account balance by the standard EVM value-transfer semantics and can never be recovered, mirroring the reported bug class of ETH becoming permanently stuck in a contract address that has no withdrawal path.

### Finding Description
Every other transaction and query handler in `precompiles/distribution/distribution.go` explicitly guards against unwanted value transfers by calling `pcommon.ValidateNonPayable(value)` (directly or via `p.validateInput`), e.g. `setWithdrawAddress` [1](#0-0) , `withdrawValidatorCommission` in `Execute` [2](#0-1) , and all the other query methods such as `params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, and `communityPool`, each of which routes through `p.validateInput(value, args, N)` [3](#0-2) .

`rewards()` is the sole exception — it never checks `value` at all: [4](#0-3) 

This gap is explicitly documented in the integration test suite's comment: "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)" [5](#0-4) .

The distribution precompile is also absent from the `payablePrecompiles` allow-list, which is reserved for precompiles designed to legitimately receive and process value (bank, staking, gov, wasmd): [6](#0-5) 

Precompiles designed to accept funds (e.g. `bank.sendNative`) immediately refund the payer via `pcommon.HandlePaymentUsei`/`HandlePaymentUseiWei` before debiting the intended amount [7](#0-6) . `rewards()` performs none of this bookkeeping — it neither rejects the value nor refunds it, so any usei/wei value attached to a `rewards(address)` call is transferred into the distribution precompile's on-chain balance by ordinary EVM CALL value-transfer semantics and then simply abandoned, since the precompile exposes no function capable of moving funds out of its own address (the distribution precompile only ever moves *rewards/commission* balances held by the distribution module, never balances sitting on the precompile's own account).

### Impact Explanation
Any EOA or contract that mistakenly (or is tricked into) sending native value with a call to `rewards(address)` on `0x...1007` will have that value permanently and irrecoverably locked in the precompile's account — there is no admin, governance, or user-facing function anywhere in the distribution precompile (or elsewhere) that can withdraw funds from that specific address. This is a permanent fund-freezing bug, matching the accepted "permanent freezing of funds" impact category.

### Likelihood Explanation
Likelihood is moderate: `rewards()` is a commonly used read function for checking a delegator's pending rewards, and DeFi aggregators or wallets that batch a value-bearing multicall (or which mistakenly forward `msg.value` when relaying arbitrary calldata) could easily trigger this path without malicious intent. Any third-party frontend/contract that doesn't specifically special-case `rewards()` as non-payable would be exposed. No privileged access is required — a single unprivileged EVM transaction from any account is sufficient.

### Recommendation
Add the same `pcommon.ValidateNonPayable(value)` check to `rewards()` that all its sibling query methods already perform, rejecting any non-zero `value` sent with the call:
```go
func (p PrecompileExecutor) rewards(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, rerr error) {
    ...
    if err := pcommon.ValidateNonPayable(value); err != nil {
        rerr = err
        return
    }
    ...
}
```
and update the `Execute` dispatch and call site accordingly. As defense in depth, consider also auditing/adding a generic sweep mechanism (or a hard EVM-level rejection) for any value sent to non-payable precompile addresses that are not in the `payablePrecompiles` allow-list.

### Proof of Concept
1. Associate an EVM address with a Sei address and delegate to a validator so rewards begin accruing (as in the existing test fixture) [8](#0-7) .
2. From that EVM account, call `rewards(delegatorAddress)` on `0x0000000000000000000000000000000000001007` while attaching `value: 1 SEI` (e.g. `{value: ethers.parseEther('1')}`).
3. Observe the call succeeds and returns the reward data normally, because `rewards()` performs no `ValidateNonPayable` check [4](#0-3) .
4. Query the balance of `0x0000000000000000000000000000000000001007` — the 1 SEI sent is now held by the precompile address with no function anywhere in the distribution precompile capable of transferring it back out.

### Citations

**File:** precompiles/distribution/distribution.go (L176-179)
```go
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
```

**File:** precompiles/distribution/distribution.go (L251-254)
```go
	if err := pcommon.ValidateNonPayable(value); err != nil {
		rerr = err
		return
	}
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

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L43-60)
```typescript
    before(async () => {
        runtime = readRuntimeState();
        admin = EvmAccount.fromMnemonic(runtime.funded.adminMnemonic, provider);
        distribution = precompileContract('distribution', admin.wallet);
        staking = precompileContract('staking', admin.wallet);
        caller = callerContract(runtime, admin.wallet);
        [validator] = await bondedValidators();

        [delegator, withdrawTarget] = claimPool(runtime, provider, 2, 'distribution:fixture');
        await associateViaTx(delegator);
        await associateViaTx(withdrawTarget);

        // Delegate so rewards start accruing; poll until they are visible.
        const tx = await (staking.connect(delegator.wallet) as ethers.Contract).delegate(
            validator,
            { value: ethers.parseEther('1'), gasLimit: 1_000_000 },
        );
        expect((await tx.wait())!.status, 'fixture delegation must succeed').to.equal(1);
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
