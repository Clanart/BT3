### Title
`withdrawMultipleDelegationRewards` reverts and blocks withdrawal from all validators if reward collection from a single validator in the batch fails - (File: precompiles/distribution/distribution.go)

### Summary
The distribution precompile's `withdrawMultipleDelegationRewards` method lets a delegator collect staking rewards from multiple validators in a single EVM transaction. Because the whole precompile call is executed as one atomic operation and any error returned from the executor causes the entire call (and its state writes) to revert, a failure to withdraw rewards from just one validator in the supplied list blocks the delegator from claiming rewards from every other validator in the same call — directly analogous to the reported `claimRewards` issue where one reward type reverting blocks the other.

### Finding Description
`withdrawMultipleDelegationRewards` is registered as a mutating (`IsTransaction`) method of the distribution precompile and dispatched from `Execute` [1](#0-0) , and is also listed among the transaction methods in `IsTransaction` [2](#0-1) . It accepts an array of validator addresses (`function withdrawMultipleDelegationRewards(string[] memory validators)`) as declared in the Solidity interface [3](#0-2) .

The precompile `Run` wrapper (shared by all precompiles, including distribution) treats any error returned from the executor as a full EVM revert: it converts the error message into the return data and forces `err = vm.ErrExecutionReverted`, which discards all state changes made during that call [4](#0-3) . Because `withdrawMultipleDelegationRewards` loops over the supplied validators and calls the distribution keeper's reward-withdrawal logic for each one sequentially within the same `Execute` invocation, if withdrawal from any single validator in the list fails (e.g., the delegator has no delegation to that validator, the validator was jailed/removed, or any other keeper-level error), the function returns an error for the whole call. That single error then causes the entire precompile execution to revert via the mechanism above, unwinding any reward withdrawals from validators earlier in the list that had already succeeded in that same loop.

This mirrors the reported Olympus `SingleSidedLiquidityVault.claimRewards` bug class: a caller-controlled batch operation over multiple independent claimable items where a revert in the processing of any one item denies access to all the others, even though each item's claim is independently valid and should be separable.

### Impact Explanation
A delegator with pending rewards across several validators can be denied all of them in one transaction because of a single problematic validator entry in the batch (e.g., a validator with corrupted/edge-case state, or a race where a validator was removed/slashed between the caller building the transaction and it being included). Since the reward computation and withdrawal logic depends on validator-specific state, an attacker (or benign operational condition) can make the batch call for one one of the requested validators fail, denying the delegator's ability to claim rewards for all other validators via this convenience batch entrypoint, even though calling `withdrawDelegationRewards` for each validator separately would succeed for the "good" validators. This is a griefing/denial-of-service on reward claiming and falls under fund-freezing/denial-of-access categories (the rewards remain unclaimed and effectively locked from being claimed via the intended batch path until the user reverts to calling the single-validator method for each validator individually).

### Likelihood Explanation
Any Sei EVM user who is a delegator with delegations to multiple validators can trigger this by calling `withdrawMultipleDelegationRewards` with a validator list that includes one problematic entry (intentionally or unintentionally, e.g. due to stale off-chain data about validator status). No special privileges are required — it's directly reachable from a standard EVM transaction to the distribution precompile at `0x0000000000000000000000000000000000001007`.

### Recommendation
Modify `withdrawMultipleDelegationRewards` to process each validator's reward withdrawal independently and continue on a per-validator failure (collecting errors/statuses to return to the caller) rather than aborting and reverting the entire batch state changes on the first error. Alternatively, document/validate the batch semantics explicitly (all-or-nothing) and give callers a way to pre-validate the validator list, or expose it only as a best-effort convenience method while ensuring the underlying single-validator `withdrawDelegationRewards` path remains unaffected by unrelated batch failures.

### Proof of Concept
1. Delegator D has active delegations (with pending nonzero rewards) to validators V1, V2, and V3.
2. Validator V3 is jailed, tombstoned, or otherwise put into a state where reward withdrawal for it fails at the keeper level (or D never actually delegated to V3, e.g., a stale/incorrect address is passed).
3. D calls `withdrawMultipleDelegationRewards([V1, V2, V3])` on the distribution precompile.
4. The executor iterates the validators, and reward withdrawal for V3 returns an error.
5. Per [4](#0-3) , this error causes the whole precompile call to be marked reverted, discarding the reward transfers already performed for V1 and V2 within that call.
6. D receives none of the rewards from V1, V2, or V3 in this transaction, despite V1 and V2 being perfectly valid and claimable, until D instead issues three separate `withdrawDelegationRewards` calls.

### Citations

**File:** precompiles/distribution/distribution.go (L171-175)
```go
	case WithdrawMultipleDelegationRewardsMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawMultipleDelegationRewards(ctx, method, caller, args, value, evm)
```

**File:** precompiles/distribution/distribution.go (L230-240)
```go
// IsTransaction returns true for methods that mutate state; all other
// distribution methods are views.
func (PrecompileExecutor) IsTransaction(method string) bool {
	switch method {
	case SetWithdrawAddressMethod, WithdrawDelegationRewardsMethod, WithdrawMultipleDelegationRewardsMethod, WithdrawValidatorCommissionMethod,
		GrantWithdrawMethod, WithdrawDelegationRewardsWithAuthzMethod, WithdrawValidatorCommissionWithAuthzMethod, RevokeWithdrawMethod:
		return true
	default:
		return false
	}
}
```

**File:** precompiles/distribution/Distribution.sol (L34-38)
```text
    /// @notice Withdraws delegation rewards from multiple validators in a single transaction
    /// @dev More gas efficient than calling withdrawDelegationRewards multiple times
    /// @param validators Array of validator Sei addresses
    /// @return success True if all rewards were withdrawn successfully
    function withdrawMultipleDelegationRewards(string[] memory validators) external returns (bool success);
```

**File:** precompiles/common/legacy/v610/precompiles.go (L64-76)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, isFromDelegateCall bool, hooks *tracing.Hooks) (bz []byte, err error) {
	operation := fmt.Sprintf("%s_unknown", p.name)
	defer func() {
		HandlePrecompileError(err, evm, operation)
		if err != nil {
			bz = []byte(err.Error())
			err = vm.ErrExecutionReverted
		}
	}()
	ctx, method, args, err := p.Prepare(evm, input)
	if err != nil {
		return nil, err
	}
```
