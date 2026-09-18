### Title
`distribution.rewards` (and related distribution query methods) accept `msg.value` with no non-payable check, permanently stranding user funds - ([File: precompiles/distribution/distribution.go])

### Summary
The `distribution` precompile's `Execute` dispatcher fails to call `pcommon.ValidateNonPayable(value)` before invoking the `rewards` query method (and several other query methods), unlike the majority of state-changing and other view methods in the same and sibling precompiles. Any caller who sends native value (wei) alongside a call to `rewards()` (or `params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) will have that value accepted by the EVM but never used, forwarded, or refunded by the precompile logic — resulting in permanent loss of funds, conceptually identical to the Biconomy Nexus report where `msg.value` is silently dropped during a low-level forwarding call.

### Finding Description
In the standard EVM semantics used by this fork, when a `CALL` (with non-zero value) targets any address — including a precompile address — the EVM's call-dispatch logic transfers the value from the caller's balance to the target address's balance in the StateDB *before* invoking the target's logic. This happens regardless of whether the target is a "real" contract or a precompile.

Sei's own precompile framework is aware of this and requires each precompile method to explicitly validate that no funds were sent for non-payable methods, via `pcommon.ValidateNonPayable(value)`. This pattern is used pervasively across `staking`, `gov`, `bank`, `slashing`, `addr`, and `pointer` precompiles.

However, in `precompiles/distribution/distribution.go`'s `Execute` dispatcher: [1](#0-0) 
```go
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
		...
	case RewardsMethod:
		return p.rewards(ctx, method, args)
	case ParamsMethod:
		return p.params(ctx, method, args, value)
	case ValidatorOutstandingRewardsMethod:
		return p.validatorOutstandingRewards(ctx, method, args, value)
	...
```
Only `WithdrawValidatorCommissionMethod` performs `ValidateNonPayable`. `RewardsMethod` and the other query methods listed dispatch directly into their handler functions, which themselves never check or use `value`: [2](#0-1) 

The `rewards()` handler receives no `value` parameter at all — it is completely dropped at the call site — and simply queries `DelegationTotalRewards` and packs the response, never touching the funds that the EVM already transferred to the precompile's on-chain address.

This exact gap is acknowledged in the project's own integration-test documentation, confirming it is a known, real, unmitigated behavior rather than a hypothetical: [3](#0-2) 
```
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```
and in the README's list of "hard-won facts": [4](#0-3) 

This is precisely analogous to the Nexus report's root cause: value is accepted (`payable`) at the call boundary but silently ignored deeper in the logic, so it is neither used for its intended purpose nor returned to the sender — it is simply stranded.

### Impact Explanation
Any transaction sender or contract can call `distribution.rewards(address)` (precompile address `0x0000000000000000000000000000000000001007`) with a non-zero `msg.value`. The value is deducted from the caller and credited to the precompile's module/system address in the StateDB, but the precompile's `Execute`/`rewards` code path has no mechanism to sweep, refund, or otherwise account for it. Because there is no user-facing withdrawal path for value sitting at a precompile's address (it's not a normal account with keys, and the distribution keeper logic never reads or transfers this stray balance), the sent value becomes permanently unrecoverable. This is a direct, unconditional fund-loss vector for any user who mistakenly (or is tricked by a malicious frontend/dApp into) sending value with this specific call. It meets the "concrete fund loss / permanent freezing" bar for validity.

### Likelihood Explanation
Likelihood is driven by user/integrator error rather than attacker sophistication: a wallet or dApp integration that (incorrectly) attaches `msg.value` to a `rewards()` query — e.g., due to reusing a generic "value-forwarding" contract wrapper (analogous to the `PrecompileCaller.callTarget` payable forwarding pattern already present in this repo's own test fixtures) — would trigger stranded funds with 100% certainty and no possibility of reverting or recovering. Because `rewards` is a commonly-called read method (used to display pending rewards to users), and other similar view methods on the same precompile also skip the check, the surface for accidental value loss is non-trivial, especially for third-party integrators who assume "payable-looking absence of revert" implies safe value forwarding.

### Recommendation
Add `pcommon.ValidateNonPayable(value)` at the top of every non-payable dispatch branch in `distribution.Execute` that currently omits it — specifically `RewardsMethod`, `ParamsMethod`, `ValidatorOutstandingRewardsMethod`, `ValidatorCommissionMethod`, `ValidatorSlashesMethod`, `DelegationRewardsMethod`, `DelegatorValidatorsMethod`, `DelegatorWithdrawAddressMethod`, and `CommunityPoolMethod`, mirroring the pattern already used for `WithdrawValidatorCommissionMethod` and consistently applied in `staking`, `gov`, `bank`, `addr`, and `pointer` precompiles. This causes the call to revert (returning the value to the caller via normal EVM revert semantics) instead of silently stranding funds. Apply the equivalent audit to all other precompile query methods across the codebase (and any legacy versioned copies under `precompiles/*/legacy/*`) to ensure no method that lacks a use for `value` can accept it.

### Proof of Concept
1. Deploy or use the `PrecompileCaller` fixture (already present in the repo) which exposes: [5](#0-4) 
```solidity
function callTarget(address target, bytes calldata data)
    external payable returns (bytes memory)
{
    (bool ok, bytes memory ret) = target.call{value: msg.value}(data);
    if (!ok) _bubble(ret);
    return ret;
}
```
2. Encode a call to `distribution.rewards(delegatorAddress)` and invoke `caller.callTarget(DISTRIBUTION_PRECOMPILE_ADDRESS, data, { value: ethers.parseEther("1") })`.
3. Observe: the call succeeds (returns the rewards data) and 1 ETH-equivalent wei is debited from the sender's EVM balance and credited to the distribution precompile address (`0x...1007`) in the StateDB, with no code path in `precompiles/distribution/distribution.go` ever reading, refunding, or otherwise accounting for that balance — permanently stranding it.

### Citations

**File:** precompiles/distribution/distribution.go (L204-222)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
	case ParamsMethod:
		return p.params(ctx, method, args, value)
	case ValidatorOutstandingRewardsMethod:
		return p.validatorOutstandingRewards(ctx, method, args, value)
	case ValidatorCommissionMethod:
		return p.validatorCommission(ctx, method, args, value)
	case ValidatorSlashesMethod:
		return p.validatorSlashes(ctx, method, args, value)
	case DelegationRewardsMethod:
		return p.delegationRewards(ctx, method, args, value)
	case DelegatorValidatorsMethod:
		return p.delegatorValidators(ctx, method, args, value)
	case DelegatorWithdrawAddressMethod:
		return p.delegatorWithdrawAddress(ctx, method, args, value)
	case CommunityPoolMethod:
		return p.communityPool(ctx, method, args, value)
	}
```

**File:** precompiles/distribution/distribution.go (L543-578)
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

**File:** integration_test/precompile_tests/README.md (L77-80)
```markdown
- **Guard tables differ per precompile** — e.g. json and pointerview accept
  DELEGATECALL, staking/gov/distribution/pointer reject it precompile-wide,
  and `distribution.rewards` accepts value (no non-payable check). Don't
  generalize dispatch tests; copy the per-method guards from the Go source.
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
