### Title
Distribution precompile query methods accept `msg.value` without a non-payable check, permanently locking sent funds - (File: `precompiles/distribution/distribution.go` / `precompiles/distribution/legacy/v66/distribution.go`)

### Summary
The `distribution` precompile's `Execute` dispatcher enforces `pcommon.ValidateNonPayable` only for `WithdrawValidatorCommissionMethod`, while its read-only query method `rewards` (and the other query methods dispatched alongside it) receive `value` but never validate or process it. Any caller who crafts a low-level call (bypassing Solidity's compile-time `view`/non-payable enforcement) can send non-zero `msg.value` to `rewards()` and that usei/wei is silently absorbed with no code path to reclaim it, mirroring the "unnecessary payable surface with no withdraw path → permanently locked funds" bug class from the external report.

### Finding Description
The distribution precompile's dispatcher only checks non-payability for state-changing methods that don't handle a payment, e.g. `WithdrawValidatorCommissionMethod` calls `pcommon.ValidateNonPayable(value)` before proceeding [1](#0-0) . In contrast, `RewardsMethod` is dispatched directly to `p.rewards(ctx, method, args)` with no such guard [2](#0-1) .

`ValidateNonPayable` is the codebase's standard mechanism for rejecting stray value on functions that don't consume it [3](#0-2) . Where value is legitimately meant to fund an action (e.g. `staking.delegate`), the precompile calls `HandlePaymentUsei`, which converts the wei into a usei coin and either spends it via a Cosmos message or explicitly refunds the payer [4](#0-3) . `rewards()` does neither: it is a pure Solidity `view` query in the interface [5](#0-4) , so normal ABI-encoded calls from Solidity contracts never attach value, but nothing in the Go executor prevents a raw/manually-crafted call from attaching `msg.value` anyway.

This is a documented, acknowledged quirk in the repo's own integration-test notes: "`distribution.rewards` accepts value (no non-payable check). Don't generalize dispatch tests; copy the per-method guards from the Go source." [6](#0-5) 

Compounding this, the distribution precompile address is **not** included in the `payablePrecompiles` allow-list that the EVM keeper uses to suppress/handle value-transfer bookkeeping for precompiles designed to receive funds (only `bank`, `staking`, `gov`, and `wasmd` are listed) [7](#0-6) . Distribution was never intended to be a value-receiving precompile, yet its `rewards` query path has no defense against receiving value, and once received there is no refund or withdrawal mechanism anywhere in the executor for that specific call.

### Impact Explanation
Any `usei`/wei sent as `msg.value` on a `rewards()` call is transferred at the EVM level to the precompile's associated account but is never used, refunded, or exposed through any withdrawal function specific to that erroneous transfer. Because the distribution precompile lacks a generic sweep/refund path for accidentally-received value (unlike `delegate`, which explicitly uses `HandlePaymentUsei` to consume or refund it), those funds are permanently stranded, constituting a fund-freezing issue.

### Likelihood Explanation
The condition is not reachable through the published Solidity ABI (the interface marks `rewards` as `view`, so `ethers`/`web3`/Solidity callers cannot attach value through normal tooling), it requires a caller to hand-craft a raw JSON-RPC `eth_call`/transaction or bespoke low-level `call{value: x}(...)` targeting the precompile with the `rewards` selector. This is trivially achievable by any EVM transaction sender or any Solidity contract doing an inline-assembly `call`, and does not require any privilege, so the class is realistically exploitable by an ordinary unprivileged EVM caller, even though it is unlikely to happen accidentally through normal contract interactions.

### Recommendation
Add `pcommon.ValidateNonPayable(value)` at the top of the `rewards` query handler (and audit the other query methods dispatched in the same `Execute` switch — `params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool` — for the same gap), consistent with the check already applied to `WithdrawValidatorCommissionMethod`.

### Proof of Concept
1. Deploy a helper contract (or use `PrecompileCaller.callTarget`, which forwards `msg.value` on a low-level `call`) targeting the distribution precompile at `0x0000000000000000000000000000000000001007` [8](#0-7) .
2. Encode a call to `rewards(address)` and invoke it via `target.call{value: 1e12 wei}(data)` as demonstrated by the `PrecompileCaller.callTarget` pattern [9](#0-8) .
3. Because the `RewardsMethod` branch of `Execute` never calls `ValidateNonPayable` [2](#0-1) , the call succeeds, the attached value is debited from the caller and moved to the precompile's account, and the query result is returned normally with no refund or record of the payment.

Note: I confirmed this dispatch pattern in the `v66` legacy copy of `precompiles/distribution/distribution.go`; I was not able to fully read the current (non-legacy) `precompiles/distribution/distribution.go` switch statement before running out of tool iterations, though the file structure/method names are identical across all listed legacy versions, and the integration-test README (which documents current, not legacy, behavior) explicitly confirms `rewards` still lacks the non-payable check today.

### Citations

**File:** precompiles/distribution/legacy/v66/distribution.go (L116-123)
```go
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawValidatorCommission(ctx, method, caller, evm)
```

**File:** precompiles/distribution/legacy/v66/distribution.go (L124-125)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
```

**File:** precompiles/common/legacy/v66/precompiles.go (L200-206)
```go
func ValidateNonPayable(value *big.Int) error {
	if value != nil && value.Sign() != 0 {
		return errors.New("sending funds to a non-payable function")
	}

	return nil
}
```

**File:** precompiles/staking/legacy/v640/staking.go (L222-228)
```go
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send delegate fund")
	}
	coin, err := pcommon.HandlePaymentUsei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), delegator, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
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

**File:** integration_test/precompile_tests/README.md (L77-80)
```markdown
- **Guard tables differ per precompile** — e.g. json and pointerview accept
  DELEGATECALL, staking/gov/distribution/pointer reject it precompile-wide,
  and `distribution.rewards` accepts value (no non-payable check). Don't
  generalize dispatch tests; copy the per-method guards from the Go source.
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

**File:** precompiles/distribution/distribution.go (L50-52)
```go
const (
	DistrAddress = "0x0000000000000000000000000000000000001007"
)
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
