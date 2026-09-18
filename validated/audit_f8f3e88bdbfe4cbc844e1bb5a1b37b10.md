### Title
`send()` in the bank precompile omits the delegatecall guard applied to `sendNative()` and other state-changing precompiles - (File: precompiles/bank/bank.go)

### Summary
`precompiles/bank/bank.go`'s `send` method authorizes token transfers solely by comparing the caller address to the registered ERC20 native pointer, without verifying that the call was not routed through a `DELEGATECALL`. Every other comparable state-changing precompile method in this codebase (`sendNative`, staking's `delegate`/`redelegate`/`undelegate`/`createValidator`/`editValidator`, and distribution's `setWithdrawAddress`/`withdrawDelegationRewards`) explicitly rejects delegatecall context before proceeding.

### Finding Description
`send()` authorizes the transfer purely via `pointer.Cmp(caller) != 0`, where `pointer` is the ERC20 native pointer registered for the denom: [1](#0-0) 

`sendNative()`, in the very same file, additionally guards against delegatecall context using `ctx.EVMPrecompileCalledFromDelegateCall()`: [2](#0-1) 

The same pattern (explicit rejection of delegatecall before any state change) is enforced in the staking and distribution precompiles via `caller.Cmp(callingContract) != 0`: [3](#0-2) [4](#0-3) 

The `isFromDelegateCall` flag is threaded from the EVM interpreter into `sdk.Context` on every precompile invocation: [5](#0-4) 

This is exactly the class of bug described in the external report: a privileged operation (moving funds on behalf of a designated caller) is authorized using a call-context assumption (`caller == pointer`) that does not account for the different code-context semantics of `DELEGATECALL` vs. a direct `CALL`. In the referenced Tokemak report, calling a swapper via `CALL` instead of `DELEGATECALL` broke the assumption that the swap executes in the liquidator's own token context; here, the *absence* of a delegatecall check on `send()` means the "only pointer can send" invariant is enforced with an incomplete identity check relative to its sibling functions, unlike `sendNative` which was deliberately hardened against this exact class of context confusion.

### Impact Explanation
If a token's registered ERC20 native pointer contract can ever execute delegatecall'd/borrowed code (e.g., via an upgrade path, a delegatecall-based module, or any code path where `address(this)` remains the pointer's address while attacker-supplied logic runs), that logic executes with `caller == pointer`, satisfying the `send()` authorization check and enabling unauthorized `MsgSend` transfers of the native denom under the pointer's identity. This falls under the accepted impact category "unauthorized transfer via precompile or pointer."

### Likelihood Explanation
Exploitability is contingent on whether the pointer contract associated with a native denom exposes any delegatecall-reachable code path (e.g., proxy/upgrade logic) that an attacker can direct to call the bank precompile's `send` method. I found references to `delegatecall` in `contracts/src/CW20ERC20Pointer.sol` (2 matches) but was not able to read its full implementation to confirm whether that delegatecall usage is attacker-influenceable in a way that reaches `bank.send`. This is a genuine gap relative to the codebase's own established defense pattern (`sendNative`, staking, distribution all enforce it), but full exploitability depends on pointer-contract internals not fully verified here.

### Recommendation
Add the same guard used in `sendNative()` to `send()`:
```go
func (p PrecompileExecutor) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call send from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall send")
	}
	...
```
Audit `contracts/src/CW20ERC20Pointer.sol` (and the other pointer contracts, `CW721ERC721Pointer.sol`, `CW1155ERC1155Pointer.sol`) to confirm no delegatecall-reachable path allows attacker-controlled bytecode to run with `address(this)` equal to a registered pointer.

### Proof of Concept
Not fully constructible from the indexed code alone: reproducing the exploit requires confirming that a registered ERC20 native pointer contract has a delegatecall path invokable by an unprivileged actor that then calls `precompiles/bank/bank.go`'s `send`. The concrete, verifiable artifact is the code-level asymmetry shown above — `send()` lacking the `EVMPrecompileCalledFromDelegateCall` check present in `sendNative()`, staking, and distribution precompiles.

### Citations

**File:** precompiles/bank/bank.go (L198-217)
```go
func (p PrecompileExecutor) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call send from staticcall")
	}
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, 0, err
	}
	denom := args[2].(string)
	if denom == "" {
		return nil, 0, errors.New("invalid denom")
	}
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, 0, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
	amount := args[3].(*big.Int)
```

**File:** precompiles/bank/bank.go (L251-257)
```go
func (p PrecompileExecutor) sendNative(ctx sdk.Context, method *abi.Method, args []interface{}, caller common.Address, callingContract common.Address, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call sendNative from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall sendNative")
	}
```

**File:** precompiles/staking/legacy/v552/staking.go (L114-124)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, _ bool, hooks *tracing.Hooks) (bz []byte, err error) {
	if readOnly {
		return nil, errors.New("cannot call staking precompile from staticcall")
	}
	ctx, method, args, err := p.Prepare(evm, input)
	if err != nil {
		return nil, err
	}
	if caller.Cmp(callingContract) != 0 {
		return nil, errors.New("cannot delegatecall staking")
	}
```

**File:** precompiles/distribution/legacy/v552/distribution.go (L106-121)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, _ bool, hooks *tracing.Hooks) (bz []byte, err error) {
	defer func() {
		if err != nil {
			state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
		}
	}()
	if readOnly {
		return nil, errors.New("cannot call distr precompile from staticcall")
	}
	ctx, method, args, err := p.Prepare(evm, input)
	if err != nil {
		return nil, err
	}
	if caller.Cmp(callingContract) != 0 {
		return nil, errors.New("cannot delegatecall distr")
	}
```

**File:** precompiles/common/precompiles.go (L64-81)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, isFromDelegateCall bool, hooks *tracing.Hooks) (bz []byte, err error) {
	operation := fmt.Sprintf("%s_unknown", p.name)
	defer func() {
		HandlePrecompileError(err, evm, operation)
		if err != nil {
			err = vm.ErrExecutionReverted
		}
	}()
	ctx, method, args, err := p.Prepare(evm, input)
	if err != nil {
		return nil, err
	}

	operation = method.Name
	em := ctx.EventManager()
	ctx = ctx.WithEventManager(sdk.NewEventManager())
	ctx = ctx.WithEVMPrecompileCalledFromDelegateCall(isFromDelegateCall)
	bz, err = p.executor.Execute(ctx, method, caller, callingContract, args, value, readOnly, evm, hooks)
```
