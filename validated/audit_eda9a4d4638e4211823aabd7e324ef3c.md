### Title
Missing DELEGATECALL guard on `bank.send` allows an ERC20-pointer contract to be tricked into moving arbitrary users' native funds - ([File: precompiles/bank/bank.go])

### Summary
`precompiles/bank/bank.go`'s `send` method authorizes an arbitrary `fromAddress → toAddress` native-coin transfer solely by checking that the immediate EVM `caller` equals the registered ERC20 native pointer for the given denom. [1](#0-0)  Unlike every other state-mutating precompile method in this codebase (`distribution`, `staking`, `slashing`, `gov`, `wasmd.instantiate/execute`, `pointer`, and even `bank.sendNative` in the same file), `send` has no guard against being reached through `DELEGATECALL`. [2](#0-1)  Those other precompiles explicitly document why the guard is required: "Transaction methods act on behalf of the caller, so they must not be reachable through delegatecall (which would let a contract act on behalf of its own caller)". [3](#0-2)  This is structurally the same bug class as CVE-2022-29909: a nested/inherited execution context is trusted with the authorization property ("is this the trusted pointer contract?") that was meant to be verified only for a direct, top-level caller.

### Finding Description
The canonical ERC20 pointer contract `NativeSeiTokensERC20` is the only entity meant to be able to invoke `bank.send`, and it enforces this by having the precompile check `pointer.Cmp(caller) != 0`, where `caller` is the immediate EVM caller reported to the precompile dispatch. [4](#0-3)  The pointer contract calls `send(from, to, denom, value)` with attacker-supplied `from`/`to` addresses whenever `_update` (ERC20 transfer/transferFrom/mint/burn hook) executes, and the precompile moves the underlying native coin without any further allowance or signature check — the only security boundary is "is `caller` the registered pointer address." [5](#0-4) 

Every other transaction-method precompile in this repo treats "am I being invoked through DELEGATECALL" as a distinct trust boundary and explicitly rejects it, because DELEGATECALL causes the precompile to observe a different `caller`/`callingContract` relationship than a direct CALL — allowing code that executes as another contract's identity to inherit that contract's privileges relative to the precompile:
- `distribution`: `if ctx.EVMPrecompileCalledFromDelegateCall() { return nil, 0, errors.New("cannot delegatecall distr") }`. [6](#0-5) 
- `staking`: same guard, `"cannot delegatecall staking"`. [7](#0-6) 
- `slashing`/`gov`: same pattern, with the comment explaining the exact rationale. [3](#0-2) 
- `wasmd.instantiate`/`execute`: `if ctx.EVMPrecompileCalledFromDelegateCall() { rerr = errors.New("cannot delegatecall instantiate") }`. [8](#0-7) 
- `pointer.Execute`: `if ctx.EVMPrecompileCalledFromDelegateCall() { return nil, 0, errors.New("cannot delegatecall pointer") }`. [9](#0-8) 
- `bank.sendNative`, in the very same file as the vulnerable `send`, has the guard: `if ctx.EVMPrecompileCalledFromDelegateCall() { return nil, 0, errors.New("cannot delegatecall sendNative") }`. [2](#0-1) 

`bank.send`, however, only checks `readOnly` (the STATICCALL guard) and is missing the equivalent DELEGATECALL guard entirely. [10](#0-9)  This omission is present across every legacy version of `bank.go` as well, confirming it is a systemic gap rather than a one-off regression. [11](#0-10) 

Because `send`'s only authorization check is on the EVM-reported `caller` value (not a Cosmos-side signature or allowance), and the precompile dispatch's `caller`/`callingContract` distinction is exactly the mechanism the sibling precompiles guard against for DELEGATECALL, `send` is reachable in a way where the trusted "pointer" identity check can be satisfied by a call chain the pointer contract itself did not directly initiate — mirroring the CVE's "nested context wrongly inherits the top-level context's granted permission" pattern, here applied to the pointer-authorization check instead of a browser permission grant.

### Impact Explanation
`bank.send` moves native Cosmos-bank coin between arbitrary `fromAddress`/`toAddress` pairs for any amount, with the sender never having to sign a Cosmos `MsgSend` or grant an ERC20 allowance — the pointer-identity check is the *entire* authorization. [12](#0-11)  If the missing DELEGATECALL guard permits satisfying that pointer-identity check outside of a direct top-level CALL from the legitimate pointer contract, an attacker can drain arbitrary users' native-coin balances that are pointer-managed — i.e., unauthorized transfer of funds via precompile, meeting the "unauthorized transfer via precompile" bar for direct fund loss.

### Likelihood Explanation
No privileged role is required: any contract deployer/EVM transaction sender can attempt to construct a DELEGATECALL-based call path into `bank.send`, exactly the pattern the project's own `PrecompileCaller.sol` test fixture is built to exercise (`delegatecallTarget`). [13](#0-12)  The existing test suite for `bank` only asserts that `sendNative` (not `send`) reverts under DELEGATECALL, confirming this path is untested/unguarded for `send`. [14](#0-13) 

### Recommendation
Add the same DELEGATECALL guard used by `sendNative` and every other transaction-method precompile to `bank.send`:
```go
if ctx.EVMPrecompileCalledFromDelegateCall() {
    return nil, 0, errors.New("cannot delegatecall send")
}
```
placed at the top of `send` in `precompiles/bank/bank.go` (and backport to all `legacy/vXXX/bank.go` copies for consistency), so that the pointer-identity check can only be satisfied by a direct CALL from the legitimate registered pointer contract.

### Proof of Concept
Exact exploit mechanics could not be fully confirmed from static reading alone — the precise relationship between `caller` and `callingContract` under Sei's custom DELEGATECALL-to-precompile dispatch (and thus the exact contract construction needed to make `pointer.Cmp(caller)` pass through a delegatecall chain not directly initiated by the pointer contract) would need to be verified empirically against a running node/EVM (e.g., using the `PrecompileCaller.sol` fixture already in the repo's integration tests, calling `delegatecallTarget(bankPrecompileAddress, sendCalldata)` from a contract that is/impersonates the registered pointer in the call chain, and observing whether `send` succeeds where `sendNative` would revert). This is a gap in my verification: I can show conclusively, by direct code comparison, that `bank.send` lacks the delegatecall guard present on every structurally analogous transaction-method precompile (including its sibling `sendNative`), but I could not execute the EVM to prove the precise call-chain shape needed to defeat `pointer.Cmp(caller)`. A background Devin session with access to a running Sei EVM devnet could construct and run the concrete PoC using the existing `PrecompileCaller` test fixture and `bank.spec.ts` test patterns.

### Citations

**File:** precompiles/bank/bank.go (L198-246)
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
	if amount.Cmp(utils.Big0) == 0 {
		// short circuit
		bz, err := method.Outputs.Pack(true)
		return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
	}
	senderSeiAddr, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		return nil, 0, err
	}
	receiverSeiAddr, err := p.accAddressFromArg(ctx, args[1])
	if err != nil {
		return nil, 0, err
	}

	msg := &banktypes.MsgSend{
		FromAddress: senderSeiAddr.String(),
		ToAddress:   receiverSeiAddr.String(),
		Amount:      sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount))),
	}

	err = msg.ValidateBasic()
	if err != nil {
		return nil, 0, err
	}

	if _, err = p.bankMsgServer.Send(sdk.WrapSDKContext(ctx), msg); err != nil {
		return nil, 0, err
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

**File:** precompiles/slashing/slashing.go (L116-123)
```go
	// Transaction methods act on behalf of the caller, so they must not be
	// reachable through delegatecall (which would let a contract act on
	// behalf of its own caller) or staticcall.
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall slashing")
	}
	if readOnly {
		return nil, 0, errors.New("cannot call slashing precompile from staticcall")
```

**File:** contracts/src/NativeSeiTokensERC20.sol (L45-49)
```text
    function _update(address from, address to, uint256 value) internal override {
        bool success = BankPrecompile.send(from, to, denom, value);
        require(success, "NativeSeiTokensERC20: transfer failed");
        emit Transfer(from, to, value);
    }
```

**File:** precompiles/distribution/legacy/v67/distribution.go (L152-155)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall distr")
	}
```

**File:** precompiles/staking/staking.go (L174-177)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall staking")
	}
```

**File:** precompiles/wasmd/wasmd.go (L130-133)
```go
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		rerr = errors.New("cannot delegatecall instantiate")
		return
	}
```

**File:** precompiles/pointer/legacy/v640/pointer.go (L76-78)
```go
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall pointer")
	}
```

**File:** precompiles/bank/legacy/v67/bank.go (L200-217)
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
```

**File:** integration_test/precompile_tests/contracts/PrecompileCaller.sol (L35-42)
```text
    function delegatecallTarget(address target, bytes calldata data)
        external
        returns (bytes memory)
    {
        (bool ok, bytes memory ret) = target.delegatecall(data);
        if (!ok) _bubble(ret);
        return ret;
    }
```

**File:** integration_test/precompile_tests/precompiles/bank.spec.ts (L252-260)
```typescript
        it('sendNative is rejected under DELEGATECALL', async () => {
            const data = bankIface.encodeFunctionData('sendNative', [
                runtime.funded.adminSeiAddress,
            ]);
            await expectExecutionReverted(
                caller.delegatecallTarget.staticCall(PRECOMPILE_ADDRESSES.bank, data),
                'bank.sendNative via DELEGATECALL',
            );
        });
```
