### Title
Distribution precompile's `rewards()` view method omits the non-payable guard, permanently stranding usei/wei sent alongside the call - (File: precompiles/distribution/distribution.go)

### Summary
The Sei EVM distribution precompile at `0x...1007` dispatches every other query and transaction method through `pcommon.ValidateNonPayable(value)` (or an equivalent payment-handling path), but the `rewards()` method is dispatched without ever inspecting `value`, so a `CALL` (not `STATICCALL`) that attaches `msg.value` to `rewards()` succeeds and the attached usei/wei is never returned to the caller or credited anywhere reachable.

### Finding Description
In `Execute`, most methods either explicitly reject non-zero value or route it through `HandlePaymentUsei`/`HandlePaymentUseiWei` to be debited/refunded correctly: [1](#0-0) 

`WithdrawValidatorCommissionMethod` calls `pcommon.ValidateNonPayable(value)` before proceeding, but `RewardsMethod` calls `p.rewards(ctx, method, args)` — note the function signature does not even take a `value` argument, so no such guard can exist for it: [2](#0-1) 

The Solidity ABI marks `rewards` as `view`: [3](#0-2) 

but Solidity's `view` keyword is only enforced by the compiler on the caller side; nothing in the Go precompile executor rejects an actual `CALL` (as opposed to `STATICCALL`) that carries `msg.value`. Under Sei's EVM, a `CALL` with non-zero value to a precompile address moves the usei/wei balance into the precompile's associated Sei account via the StateDB bridge before/at the point the interpreter invokes the precompile; since `rewards()` never calls `HandlePaymentUsei`/`HandlePaymentUseiWei` to refund the payer (unlike `deposit()` in the gov precompile or the wasmd `execute`/`executeBatch` handlers which explicitly refund and validate value against declared payment amounts), the transferred funds are left credited to the distribution precompile's underlying account with no execution path in the precompile to return or otherwise use them.

This is directly acknowledged in the project's own integration test comments as a known guard gap: [4](#0-3) [5](#0-4) 

By contrast, every comparable read/query dispatch elsewhere in the same file (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) passes `value` into a `validateInput` helper that presumably enforces non-payability, and other precompiles (`bank.send`, `addr.getSeiAddr`/`associate`, gov's `deposit`) either explicitly reject or explicitly account for value. `rewards()` is the sole outlier that both accepts value implicitly and drops it silently.

### Impact Explanation
Any unprivileged EVM caller who sends a transaction to `0x...1007` invoking `rewards(address)` with non-zero `msg.value` will have that usei/wei permanently locked: the value is transferred by the EVM/StateDB bridge as part of executing the `CALL`, but the precompile logic that runs afterward neither validates nor refunds it, and there is no other public entry point that can move funds out of the precompile's backing account on the caller's behalf. This is a straightforward permanent fund-freezing bug reachable by any transaction sender with no special privileges — meeting the Medium severity bar for concrete permanent freezing of funds.

### Likelihood Explanation
Trivial to trigger: any address can call `rewards(address)` on the distribution precompile with a non-zero `value` field via a plain EVM transaction (not a `staticcall`), whether accidentally (e.g., wallet/contract code that defaults to attaching value) or through direct exploitation/griefing of another user's funds by proxying calls. No association, permissions, or special state are required beyond having usei to send.

### Recommendation
Add a `pcommon.ValidateNonPayable(value)` (or equivalent) check at the top of `rewards()` in `precompiles/distribution/distribution.go`, consistent with every other pure/query method in the same executor, and thread `value` into its signature so the guard can be applied at the `Execute` dispatch switch as well. Add a regression/integration test asserting that a value-bearing call to `rewards()` reverts (mirroring the "view rejects value" tests that already exist for the other query methods).

### Proof of Concept
1. Deploy/use a simple caller contract or send a raw transaction that performs a `CALL` (not `eth_call`/`staticcall`) to the distribution precompile address `0x0000000000000000000000000000000000001007` with calldata `rewards(address)` and `value = 1 usei` (converted to wei).
2. Observe the transaction succeeds and returns the delegator's rewards data.
3. Query the sender's usei balance before/after: the `value` sent has been deducted and is not credited back to the sender nor is it retrievable through any distribution-precompile method — it is stranded at the precompile's backing account permanently.

Note: I was not able to directly inspect the exact StateDB/ante-handler code that performs the usei/wei value transfer for precompile calls (the `state` package's bridge logic) within the available search results, so the precise mechanics of how `msg.value` is credited before `Execute` runs are inferred from the existing `HandlePaymentUsei`/`HandlePaymentUseiWei` refund pattern used by every other payable precompile method, combined with the project's own test-suite comment explicitly calling out `rewards()` as lacking the non-payable guard and warning that "a value-bearing call would succeed and strand the funds."

### Citations

**File:** precompiles/distribution/distribution.go (L176-205)
```go
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawValidatorCommission(ctx, method, caller, evm)
	case GrantWithdrawMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.grantWithdrawAuthorization(ctx, method, caller, args, value)
	case WithdrawDelegationRewardsWithAuthzMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawDelegationRewardsWithAuthorization(ctx, method, caller, args, value, evm)
	case WithdrawValidatorCommissionWithAuthzMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawValidatorCommissionWithAuthorization(ctx, method, caller, args, value, evm)
	case RevokeWithdrawMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.revokeWithdrawAuthorization(ctx, method, caller, args, value)
	case RewardsMethod:
		return p.rewards(ctx, method, args)
```

**File:** precompiles/distribution/legacy/v640/distribution.go (L322-360)
```go
}

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

**File:** precompiles/distribution/Distribution.sol (L79-83)
```text
    /// @notice Gets all pending rewards for a delegator
    /// @dev Returns rewards from all validators the address has delegated to
    /// @param delegatorAddress The EVM address of the delegator
    /// @return rewards Structured data containing all pending rewards
    function rewards(address delegatorAddress) external view returns (Rewards memory rewards);
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

**File:** integration_test/precompile_tests/README.md (L76-80)
```markdown
  can only be exercised via `eth_call`/`staticCall`, never a real tx.
- **Guard tables differ per precompile** — e.g. json and pointerview accept
  DELEGATECALL, staking/gov/distribution/pointer reject it precompile-wide,
  and `distribution.rewards` accepts value (no non-payable check). Don't
  generalize dispatch tests; copy the per-method guards from the Go source.
```
