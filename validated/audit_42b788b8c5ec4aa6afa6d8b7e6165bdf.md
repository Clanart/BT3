### Title
Bank precompile `send()` is missing the delegatecall guard present on every other state-mutating precompile method - ([File: precompiles/bank/bank.go])

### Summary
Every other transaction-mutating precompile method in sei-chain (`staking`, `gov`, `distribution`, `pointer`, `wasmd.instantiate`, and even `bank.sendNative` in the same file) explicitly rejects execution when the precompile is reached via `DELEGATECALL`, using either `ctx.EVMPrecompileCalledFromDelegateCall()` or the `caller.Cmp(callingContract) != 0` heuristic. The `bank.send()` method — which is gated purely on an identity check (`pointer.Cmp(caller)`) — has no such guard, breaking the pattern established across the rest of the precompile surface.

### Finding Description
The bank precompile's `send` method is used by ERC20 "native pointer" contracts to move `usei`-denominated coins on behalf of users. Its only access control is: [1](#0-0) 

```go
pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
if !exists || pointer.Cmp(caller) != 0 {
    return nil, 0, fmt.Errorf("only pointer %s can send %s but got %s", ...)
}
```
This relies entirely on `caller` (the observed `msg.sender`) matching the registered pointer contract address for the denom.

In contrast, `sendNative` — the sibling transaction method in the exact same file — explicitly blocks delegatecall: [2](#0-1) 

And every other precompile with an identity-based authorization check (staking, gov, distribution, pointer) enforces the same protection via `caller.Cmp(callingContract) != 0`: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

The `bank.send` `Execute` dispatch does not apply either check before calling `p.send(...)`: [7](#0-6) 

Because `send()`'s only authorization is "does `caller` (msg.sender as observed by the precompile) equal the registered pointer address", and `DELEGATECALL` preserves `msg.sender` from the outer call frame while changing only `address(this)`, this check can be satisfied without the legitimate pointer contract's code path (allowance decrement, balance bookkeeping, event emission) ever running — exactly the class of bug in the report, where an identity/target check is bypassed because the guard fails to account for delegatecall semantics.

### Impact Explanation
If any code path allows the registered ERC20 native pointer contract for a denom to be induced into performing an external call (e.g., a token hook, callback, or forwarding call) to an attacker-supplied address, an attacker-controlled contract at that address could `DELEGATECALL` straight into the bank precompile's `send` method. Since delegatecall preserves `msg.sender` from the pointer's own call, the precompile would observe `caller == pointer`, satisfying the sole authorization check and allowing the attacker to move `usei` coins registered under that denom directly via `MsgSend`, bypassing the pointer contract's actual ERC20 semantics (allowances, balances, `Transfer` events) entirely. This is an unauthorized-transfer-via-precompile scenario, which is an accepted impact.

### Likelihood Explanation
This requires the legitimate ERC20 native pointer contract to make an external call into an address effectively chosen or influenced by the attacker, and I was not able to confirm from the available index whether the pointer contract implementation contains such a call path (the pointer contract's own source was not fully inspected in this session). Regardless of that specific chaining requirement, the underlying guard gap is confirmed by direct code comparison: `send()` lacks the delegatecall check that every structurally-identical identity-gated precompile method in this codebase enforces, which is a genuine inconsistency and latent attack surface.

### Recommendation
Add the same guard used by `sendNative` and the other precompiles to `bank.send`, e.g.:
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
More generally, audit all precompile methods that authorize based solely on `caller` equality (rather than `caller.Cmp(callingContract)` or an explicit `ctx.EVMPrecompileCalledFromDelegateCall()` check) to ensure the delegatecall guard is applied consistently across every state-mutating method, not just some.

### Proof of Concept
Exact reproduction was not completed in this session because it depends on whether the CW/ERC20 native pointer contract implementation ever performs an external call to an attacker-influenced address (not confirmed from the indexed files). The concrete, verifiable part of the finding is the code-level inconsistency itself:
1. `precompiles/bank/bank.go` `send()` (lines 198-216) authorizes purely via `pointer.Cmp(caller)`.
2. `precompiles/bank/bank.go` `sendNative()` (lines 251-257), in the same file, explicitly checks `ctx.EVMPrecompileCalledFromDelegateCall()` before doing similar sensitive work.
3. All other identity-gated, state-mutating precompiles (`staking`, `gov`, `distribution`, `pointer`) enforce `caller.Cmp(callingContract) != 0` specifically to prevent the delegatecall-based identity-preservation bypass.

A Devin agent with code execution access should write a Go unit test analogous to `TestStakingPrecompileDelegateCallPrevention` (`precompiles/staking/staking_test.go`, lines 2417-2497), but for `bank.PrecompileExecutor.Execute` with `SendMethod`, asserting that calling `send` with `ctx.WithEVMPrecompileCalledFromDelegateCall(true)` (or `caller != callingContract`) is rejected — and confirm today it is NOT rejected, unlike the staking/gov/distribution/pointer equivalents.

### Citations

**File:** precompiles/bank/bank.go (L167-169)
```go
	switch method.Name {
	case SendMethod:
		return p.send(ctx, caller, method, args, value, readOnly)
```

**File:** precompiles/bank/bank.go (L198-216)
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

**File:** precompiles/gov/legacy/v555/gov.go (L109-124)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, _ bool, hooks *tracing.Hooks) (bz []byte, err error) {
	defer func() {
		if err != nil {
			state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
		}
	}()
	if readOnly {
		return nil, errors.New("cannot call gov precompile from staticcall")
	}
	ctx, method, args, err := p.Prepare(evm, input)
	if err != nil {
		return nil, err
	}
	if caller.Cmp(callingContract) != 0 {
		return nil, errors.New("cannot delegatecall gov")
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

**File:** precompiles/pointer/legacy/v555/pointer.go (L112-127)
```go
func (p Precompile) RunAndCalculateGas(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, suppliedGas uint64, value *big.Int, hooks *tracing.Hooks, readOnly bool, _ bool) (ret []byte, remainingGas uint64, err error) {
	defer func() {
		if err != nil {
			state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
		}
	}()
	if readOnly {
		return nil, 0, errors.New("cannot call pointer precompile from staticcall")
	}
	ctx, method, args, err := p.Prepare(evm, input)
	if err != nil {
		return nil, 0, err
	}
	if caller.Cmp(callingContract) != 0 {
		return nil, 0, errors.New("cannot delegatecall pointer")
	}
```
