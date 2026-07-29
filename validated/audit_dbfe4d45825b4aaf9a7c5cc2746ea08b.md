### Title
Native token value attached to non-payable staking/gov/distribution/slashing/bank precompile methods is permanently locked - ([File: precompiles/staking/staking.go])

### Summary
The `PuttyV2` bug class ("payable function accepts `msg.value` on a code path that never consumes it, permanently locking the Ether") maps directly onto Cosmos EVM's stateful precompile dispatch. `x/vm` allows any EVM `CALL` to attach `value` to a precompile address regardless of the target method's intended payability, because Solidity's compiler-level `payable` check only protects high-level calls, not raw calls. The `erc20` precompile explicitly defends against this by rejecting any call carrying `contract.Value() > 0`, but the `staking` precompile's `Execute` dispatcher (and, by the same pattern, `gov`, `slashing`, and `bank`) does **not** perform this check for methods that don't use the transferred value (e.g. `Undelegate`, `Redelegate`, `CancelUnbondingDelegation`, `EditValidator`).

### Finding Description
`precompiles/erc20/erc20.go` `Execute()` contains an explicit guard: [1](#0-0) 
This guard exists specifically because a precompile has no EOA/private key, so any native balance credited to it can never be spent back out — it is provably unrecoverable.

`precompiles/staking/staking.go` `Execute()` dispatches to multiple methods without any equivalent check: [2](#0-1) 
Only `Delegate` and `CreateValidator` are documented/designed to consume `msg.value` (they forward `contract.Value()`/`msg.value` into the delegation/validator-creation amount, as seen in the Solidity test callers using `staking.STAKING_CONTRACT.delegate(..., msg.value)`): [3](#0-2) 
`Undelegate`, `Redelegate`, `CancelUnbondingDelegation`, and `EditValidator` never reference `contract.Value()` and are called from Solidity with plain `public` (non-payable) signatures, e.g.: [4](#0-3) 

Because the ABI-level `payable`/non-payable distinction is a Solidity compile-time convenience and is not re-validated inside the Go precompile dispatcher (`cmn.SetupABI` performs no value check either — see `precompiles/common/precompile.go` `SetupABI`), a caller can construct a raw low-level call (`address(STAKING_PRECOMPILE).call{value: X}(abi.encodeWithSelector(Undelegate.selector, ...))`) that attaches native value to one of these unconsumed-value methods. The EVM's standard value-transfer semantics move `X` aatom from the caller to the precompile's address balance before `Run`/`Execute` is invoked, exactly as it would for the `erc20`/`werc20` precompiles. Since the staking precompile address is not an EOA and no code path ever spends or refunds that balance (no `fallback`/`receive`, no explicit bank transfer back to caller), the funds become permanently stranded at the precompile address.

### Impact Explanation
This is a Critical permanent loss/freezing of user native-token funds: value sent to the staking precompile via `Undelegate`, `Redelegate`, `CancelUnbondingDelegation`, or `EditValidator` (and any structurally similar dispatcher in `gov`, `slashing`, or `bank` precompiles lacking the same `erc20`-style guard) is irrecoverably locked, since the precompile has no private key and no withdrawal mechanism for stray balances credited to it. This matches the allowed "Critical permanent freezing/locking/theft of user funds" impact class, and is directly analogous to the confirmed Putty Finance M-05 finding and its official mitigation (adding an explicit `msg.value == 0` check).

### Likelihood Explanation
Triggerable by any unprivileged user via a single low-level call/transaction; requires only that the caller (an EOA misusing a wallet UI, or more commonly a malicious/buggy intermediary smart contract that forwards `msg.value` indiscriminately) attaches value to a call selecting one of the non-value-consuming staking methods. No special privileges, races, or validator/relayer cooperation are required — only a raw call bypassing the Solidity interface's `payable` restriction, which is trivial to construct.

### Recommendation
Apply the same defense used in the `erc20` precompile to every stateful precompile whose methods do not consume `contract.Value()`: at the top of each precompile's `Execute()` (or centrally in `cmn.SetupABI`), reject the call if `contract.Value().Sign() > 0` and the resolved method is not one of the value-consuming ones (`Delegate`, `CreateValidator`, `FundCommunityPool`/deposit-style methods, etc.). Concretely, extend `staking.Precompile.Execute` to check `contract.Value()` before dispatching to `Undelegate`, `Redelegate`, `CancelUnbondingDelegation`, and `EditValidator`, and audit `gov`, `slashing`, and `bank` precompiles for the same missing guard.

### Proof of Concept
1. Deploy a helper contract (or use a raw signed transaction) that performs:
   `STAKING_PRECOMPILE_ADDRESS.call{value: 1 ether}(abi.encodeWithSelector(UndelegateMethod.selector, delegatorAddr, validatorAddr, amount))`
2. The EVM engine transfers `1 ether` (in `aatom`) from the caller to the staking precompile address as part of normal `CALL` value semantics before `Precompile.Run`/`Execute` runs.
3. `Execute()` dispatches to `Undelegate`, which never reads or forwards `contract.Value()`; the undelegate operation completes successfully and the transaction is not reverted.
4. Query the native/`aatom` balance of the staking precompile address (`evmtypes.StakingPrecompileAddress`) post-transaction — it now holds `1 ether` that cannot be withdrawn by any subsequent transaction, since the precompile exposes no method that spends its own balance and it has no private key for a signed transfer.

### Citations

**File:** precompiles/erc20/erc20.go (L148-156)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// ERC20 precompiles cannot receive funds because they are not managed by an
	// EOA and will not be possible to recover funds sent to an instance of
	// them.This check is a safety measure because currently funds cannot be
	// received due to the lack of a fallback handler.
	if value := contract.Value(); value.Sign() == 1 {
		return nil, fmt.Errorf(ErrCannotReceiveFunds, contract.Value().String())
	}

```

**File:** precompiles/staking/staking.go (L99-121)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	var bz []byte

	switch method.Name {
	// Staking transactions
	case CreateValidatorMethod:
		bz, err = p.CreateValidator(ctx, contract, stateDB, method, args)
	case EditValidatorMethod:
		bz, err = p.EditValidator(ctx, contract, stateDB, method, args)
	case DelegateMethod:
		bz, err = p.Delegate(ctx, contract, stateDB, method, args)
	case UndelegateMethod:
		bz, err = p.Undelegate(ctx, contract, stateDB, method, args)
	case RedelegateMethod:
		bz, err = p.Redelegate(ctx, contract, stateDB, method, args)
	case CancelUnbondingDelegationMethod:
		bz, err = p.CancelUnbondingDelegation(ctx, contract, stateDB, method, args)
	// Staking queries
```

**File:** precompiles/staking/testdata/StakingCaller.sol (L69-83)
```text
    /// @dev This function calls the staking precompile's delegate method.
    /// delegator must call this function with the native coin value he wants to delegate.
    /// @param _validatorAddr The validator address to delegate to.
    function testDelegate(
        string memory _validatorAddr
    ) public payable {
        _dequeueUnbondingDelegation();
        bool success = staking.STAKING_CONTRACT.delegate(
            address(this),
            _validatorAddr,
            msg.value
        );
        require(success, "delegate failed");
        _increaseAmount(msg.sender, _validatorAddr, msg.value);
    }
```

**File:** precompiles/staking/testdata/StakingCaller.sol (L85-98)
```text
    /// @dev This function calls the staking precompile's undelegate method.
    /// @param _validatorAddr The validator address to delegate to.
    /// @param _amount The amount to delegate.
    function testUndelegate(
        string memory _validatorAddr,
        uint256 _amount
    ) public {
        _checkDelegation(_validatorAddr, _amount);
        _dequeueUnbondingDelegation();
        int64 completionTime = staking.STAKING_CONTRACT.undelegate(address(this), _validatorAddr, _amount);
        require(completionTime > 0, "Failed to undelegate");
        _undelegate(_validatorAddr, _amount, completionTime);
    }

```
