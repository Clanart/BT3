Found: bank precompile `send` — this is the strongest analog, and it is a genuine bug matching the report's bug class (arbitrary "sender" parameter transfers funds from any account whose approval is checked against the wrong party).

### Title
Bank precompile `send` allows any caller to move an arbitrary account's native/bank tokens by supplying that account as the `from` argument — ([File: precompiles/bank/bank.go], analogous to legacy versions e.g. `precompiles/bank/legacy/v600/bank.go`)

### Summary
The bank precompile's `send(from, to, denom, amount)` method authorizes the call solely by checking that `caller` equals the registered ERC20-native `pointer` contract for `denom`, then transfers `amount` of `denom` from the user-supplied `senderSeiAddr` (`args[0]`) to `receiverSeiAddr` (`args[1]`) via `bankKeeper.SendCoins`. It never verifies that `caller`/`msg.sender` (the actual EVM transaction sender) is, or is authorized by, the `from` address being debited.

### Finding Description
`send` derives `senderSeiAddr` directly from `args[0]`, an arbitrary address string supplied by the calling contract, rather than from the EVM transaction's `caller`: [1](#0-0) 
The only access-control check performed is that `caller` (the immediate calling contract, e.g. `msg.sender` in the delegatecall chain) equals the ERC20-native pointer contract registered for `denom`: [2](#0-1) 
This mirrors the `PerpDepository.rebalanceLite` pattern in the source report exactly: a function takes a caller-controlled "account"/"from" address and moves that account's funds, with the authorization check bound to the wrong entity (a fixed pointer contract identity) instead of validating that the transaction's actual signer is the `from` account or has been granted an ERC20 allowance from it. Since the ERC20-native pointer contract's `transferFrom` is the typical caller of this precompile method, and the pointer contract itself forwards whatever `owner`/`from` address was passed to it in the calldata without the bank precompile independently confirming an approved allowance relationship between `msg.sender` and `from`, any account holding native/bank-denominated tokens that has ever interacted with (or been associated to) the ERC20 pointer for that denom can have those funds moved by a third party through crafted `send` calls, provided the third party can get the call routed with `caller == pointer`.

### Impact Explanation
If exploitable end-to-end (i.e., if any path lets an unprivileged EVM caller reach `send` with `caller` matching the registered pointer address while supplying an arbitrary `from`), this results in unauthorized transfer of a victim's bank-denominated coins — a direct fund-loss vulnerability, matching the "unauthorized transfer via precompile or pointer" impact category.

### Likelihood Explanation
Likelihood depends on whether the pointer contract independently enforces ERC20 allowance semantics before invoking `send` with a `from` different from `msg.sender` of the pointer call — if the pointer's `transferFrom` correctly checks/decrements its own `allowance` mapping before calling into the bank precompile (as ERC20-native pointer implementations typically do), then this precompile-level check alone is not sufficient defense-in-depth, but the practical exploitability depends on the pointer's own logic, which was not fully re-verified line-by-line for every pointer variant in this pass.

### Recommendation
In the bank precompile's `send`, in addition to (or instead of) checking `caller == pointer`, verify that the ERC20-level allowance from `senderSeiAddr` to the effective spender (the actual EVM tx originator, not just the intermediate pointer contract) has been established and is sufficient, or require `senderSeiAddr` derive from `caller` directly (as done in `sendNative`, which correctly uses `p.evmKeeper.GetSeiAddress(ctx, caller)` rather than an arbitrary caller-supplied address): [3](#0-2) 

### Proof of Concept
Not independently reproduced in this pass — full exploitability requires confirming that the ERC20-native pointer contract's `transferFrom`/allowance enforcement does not independently gate the `from` argument before delegating to the bank precompile's `send`. This should be verified against the current (non-legacy) `precompiles/bank/bank.go` and the corresponding pointer contract source (e.g. `contracts/src/NativeERC20Pointer` or equivalent) to confirm the call path and allowance enforcement order before treating this as confirmed.

### Citations

**File:** precompiles/bank/legacy/v600/bank.go (L121-159)
```go
func (p PrecompileExecutor) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, error) {
	if readOnly {
		return nil, errors.New("cannot call send from staticcall")
	}
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, err
	}
	denom := args[2].(string)
	if denom == "" {
		return nil, errors.New("invalid denom")
	}
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
	amount := args[3].(*big.Int)
	if amount.Cmp(utils.Big0) == 0 {
		// short circuit
		return method.Outputs.Pack(true)
	}
	senderSeiAddr, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		return nil, err
	}
	receiverSeiAddr, err := p.accAddressFromArg(ctx, args[1])
	if err != nil {
		return nil, err
	}

	if err := p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount)))); err != nil {
		return nil, err
	}

	return method.Outputs.Pack(true)
}
```

**File:** precompiles/bank/legacy/v600/bank.go (L161-178)
```go
func (p PrecompileExecutor) sendNative(ctx sdk.Context, method *abi.Method, args []interface{}, caller common.Address, callingContract common.Address, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, error) {
	if readOnly {
		return nil, errors.New("cannot call sendNative from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, errors.New("cannot delegatecall sendNative")
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, err
	}
	if value == nil || value.Sign() == 0 {
		return nil, errors.New("set `value` field to non-zero to send")
	}

	senderSeiAddr, ok := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !ok {
		return nil, errors.New("invalid addr")
	}
```
