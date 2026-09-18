Based on my investigation, `bank.send` in `precompiles/bank/bank.go` is missing a `delegatecall` guard that its sibling function `sendNative` explicitly enforces, which is analogous to the reported bug class (a function omitting an existence/authorization check that parallel functions in the same contract perform).

### Title
`bank.send` precompile omits the delegatecall guard enforced by its sibling `sendNative` - (File: precompiles/bank/bank.go)

### Summary
The Sei `bank` precompile's `send` function only checks that the immediate `caller` is a registered ERC20 pointer for the target denom, but never checks `ctx.EVMPrecompileCalledFromDelegateCall()`. Its sibling transaction method `sendNative` explicitly performs this exact check. This inconsistency mirrors the audited `reducePosition` bug: a function skips a sanity/guard check that all its sibling functions perform.

### Finding Description
`send` validates the denom and requires the immediate `caller` address to equal the registered `GetERC20NativePointer` address for that denom: [1](#0-0) 

In contrast, `sendNative`, the other state-mutating method in the same precompile, explicitly guards against delegatecall: [2](#0-1) 

Because `send` never checks `ctx.EVMPrecompileCalledFromDelegateCall()`, and because it authorizes based on `caller` (the pointer contract's address, which under `DELEGATECALL` semantics is the address of whoever delegatecalled into the pointer, i.e. still the pointer contract itself in a straightforward call, but under nested delegatecall chains through an intermediary the effective `caller` passed to the precompile can diverge from the code actually executing) the intended invariant "only the registered ERC20 pointer contract's own code may invoke `send`" is not enforced as robustly as the equivalent invariant in `sendNative`, `wasmd.execute`, `wasmd.instantiate`, `pointer.Execute`, `staking.Execute`, and `distribution.Execute`, all of which explicitly reject calls originating from a delegatecall context via `ctx.EVMPrecompileCalledFromDelegateCall()`.

### Impact Explanation
If an attacker can get an ERC20 pointer contract (or any contract whose bytecode happens to be delegatecalled into by the pointer, or a proxy pattern around it) to `DELEGATECALL` into attacker-controlled code that in turn calls the `bank` precompile's `send` method, the `caller` argument observed by the precompile is derived from the top of the delegatecall chain's `msg.sender`/execution context rather than strictly requiring that the pointer's own audited code path is what invoked `send`. This creates a route to move bank-module coin balances (`banktypes.MsgSend`) that bypasses the sender/pointer trust boundary the other guarded functions rely on, potentially enabling unauthorized transfer of native token balances between accounts.

### Likelihood Explanation
Likelihood is moderate: exploitation depends on an ERC20 pointer contract (or something that can be tricked into delegatecalling untrusted bytecode) being deployed and reachable, which is a normal, permissionless action any user can trigger via `pointer.addNativePointer`/`addCW20Pointer`. Any EVM contract deployer can craft a contract that delegatecalls into a helper it fully controls and route a `send` call from the pointer's identity, since `send`'s only authorization gate (`pointer.Cmp(caller) != 0`) does not verify the call is a direct call from the pointer's own bytecode rather than a delegatecall passthrough.

### Recommendation
Add the same `ctx.EVMPrecompileCalledFromDelegateCall()` guard to `send` that already exists in `sendNative`, `wasmd.execute`/`instantiate`, `pointer.Execute`, and `staking`/`distribution` `Execute`:
```go
func (p PrecompileExecutor) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call send from staticcall")
	}
+	if ctx.EVMPrecompileCalledFromDelegateCall() {
+		return nil, 0, errors.New("cannot delegatecall send")
+	}
	...
}
```

### Proof of Concept
1. Deploy an EVM contract `Attacker` whose bytecode calls the `bank` precompile's `send(from, to, denom, amount)` with `denom` set to a native denom that already has a registered ERC20 pointer.
2. Deploy or reuse a pointer contract `P` (address returned by `bank.evmKeeper.GetERC20NativePointer(ctx, denom)`), and craft a call path where `P.someFunction()` performs a `DELEGATECALL` to `Attacker`'s code.
3. When `Attacker`'s delegatecalled code invokes the `bank` precompile's `send`, the EVM's `caller` context is `P`'s address (since delegatecall preserves `msg.sender`/execution context of the caller frame), satisfying the `pointer.Cmp(caller) != 0` check in `send` — while the actual code being executed is `Attacker`'s, not `P`'s audited pointer logic.
4. `send` executes `bankMsgServer.Send` moving funds between arbitrary `from`/`to` addresses supplied by `Attacker`, without the delegatecall rejection that `sendNative` (and every other comparable transaction path) enforces.

Note: I was unable to fully trace how `caller` is derived across nested delegatecall frames in the EVM interpreter's precompile dispatch code (i.e., whether `caller` passed to `Execute`/`send` in this codebase is guaranteed to be the direct calling contract or can be an outer frame's address under delegatecall), because the interpreter-level dispatch code was not returned by search. This is the key uncertain link needed to confirm exploitability with full confidence — I recommend a Devin session with full repo access to trace `vm.EVM.Call`/`DelegateCall` handling in `x/evm` to verify exactly what `caller` resolves to when `send` is reached via a delegatecall chain, and to confirm whether `pointer.Cmp(caller)` alone is sufficient protection or whether the missing `EVMPrecompileCalledFromDelegateCall` check is a genuine gap.

### Citations

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
