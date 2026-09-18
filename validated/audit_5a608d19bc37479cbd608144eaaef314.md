## Analysis

The external report's core bug class is: a semi-trusted external call (the swap) is trusted to not re-enter and corrupt state that a caller depends on for balance/share invariant checks. The strongest analog reachable by an ordinary EVM transaction sender in sei-chain is the **EVM → CosmWasm → EVM reentrancy hole** in the wasmd precompile / `CallEVM` bridge.

sei-chain intentionally blocks the EVM→CW→EVM pattern in the general case: [1](#0-0) 

but it explicitly carves out an exception whenever the CW contract was reached *through the wasmd precompile* (`ctx.EVMEntryViaWasmdPrecompile()==true`), which is exactly the path used by an ordinary EVM contract calling `WASMD_PRECOMPILE_ADDRESS.execute(...)`: [2](#0-1) 

Once inside that CW execution, the contract can emit a `CallEVM` custom message (unrestricted target, unlike `DelegateCallEvm` which is pointer-whitelisted): [3](#0-2) [4](#0-3) 

`HandleInternalEVMCall` → `CallEVM` starts a brand-new top-level EVM state transition mid-flight of the original EVM transaction, with no target restriction (only the `solo` precompile is excluded): [5](#0-4) 

### Title
EVM→CW→EVM re-entrancy via wasmd precompile `execute` allows a malicious CosmWasm callee to reenter the caller's EVM state mid-transaction - (File: x/evm/keeper/evm.go)

### Summary
Any EVM contract that calls an arbitrary CosmWasm contract through the wasmd precompile's `execute` method (address `0x...1002`) can be re-entered: the invoked CW contract can emit a `CallEVM` message that is dispatched back into the EVM as a fresh top-level state transition, targeting any EVM contract (including the original caller or any shared protocol contract), before the wasmd `execute` call returns to the caller. This mirrors the external report's core issue — a caller trusts that a semi-trusted external call (there: `_swapper.swap()`; here: `wasmd.execute()` to a CW contract) cannot mutate shared protocol state mid-call, but a general EVM→CW→EVM reentrancy path exists and is explicitly permitted for calls that originate via the wasmd precompile.

### Finding Description
`CallEVM` guards against EVM→CW→EVM in general:
```go
if ctx.IsEVM() && !ctx.EVMEntryViaWasmdPrecompile() {
    return nil, errors.New("sei does not support EVM->CW->EVM call pattern")
}
``` [6](#0-5) 

However, when the CW contract is reached via the wasmd precompile's `execute`/`executeBatch`/`instantiate` methods called directly from an EVM contract, `EVMEntryViaWasmdPrecompile()` is true, so the guard is bypassed. Within that CW execution, the contract can return a `CallEVM` (regular call, not delegatecall) custom message that is unpacked in `wasmbinding/message_plugin.go` and routed to `HandleInternalEVMCall`, which calls `CallEVM` with no restriction on the target address other than excluding the `solo` precompile: [4](#0-3) [7](#0-6) 

This nested `CallEVM` executes a full independent `applyEVMMessage` against the live EVM/Cosmos state — effectively a synchronous callback into arbitrary EVM contract state — while the *original* caller contract's `wasmd.execute()` call is still on the stack, unresolved. Any EVM contract on Sei that performs a check-before/check-after pattern around a call to the wasmd precompile (e.g., "read balance/shares, call execute() to have a CW counterpart move value, read balance/shares again, assert the delta") can have that invariant defeated: the CW contract invoked mid-call can trigger `CallEVM` to mutate the same balances/shares (e.g., via a Bank precompile `send`, or a shared pointer/vault contract) before the outer call returns, exactly the class of manipulation the external report warns about for "the total amount of shares changing during the operation" and "not trusting the return value of an external call."

Unlike `MsgInternalEVMDelegateCall`, which is restricted to whitelisted pointer contracts calling back only into their own pointer address, `MsgInternalEVMCall` has no such whitelist, so this reentrancy is reachable by any EVM contract deployer who also deploys/controls (or tricks a caller into calling) a CosmWasm contract.

### Impact Explanation
An attacker-controlled CosmWasm contract, reachable from any EVM contract that calls the wasmd precompile's `execute`, can reenter the EVM state during that call. If any Sei-deployed DeFi/vault-style contract (analogous to the periphery manager's rebalancing/rewards logic in the external report) relies on `wasmd.execute()` to move CW-side value and then checks invariants (balances, total supply, "no funds extracted beyond expected slippage") without a reentrancy guard, an attacker can extract funds or corrupt shared accounting mid-transaction. This is direct fund-loss potential mediated purely by transaction/contract calls — within the medium/high impact bar (unauthorized transfer / fund loss via precompile).

### Likelihood Explanation
Likelihood depends entirely on whether any deployed contract calls the wasmd precompile's `execute` against attacker-influenced or attacker-deployable CW contract addresses without a `nonReentrant` guard around the call. Since the wasmd precompile is a first-class, documented interoperability mechanism (see `x/evm/AGENTS.md`) intended for arbitrary CW interaction from EVM contracts, and the exception to the EVM→CW→EVM block is explicit and by-design for this path, any protocol contract using this pattern without its own reentrancy protection is exposed. The precompile itself does not add a reentrancy guard, pushing the burden entirely onto caller contracts.

### Recommendation
1. Add a global reentrancy guard (or per-transaction call-depth/exec-flag) inside `CallEVM`/`HandleInternalEVMCall` so that a `CallEVM` message dispatched while already inside an EVM-originated `wasmd.execute()` call cannot target the originating caller contract (or, more conservatively, cannot re-enter any EVM contract at all while `ctx.EVMEntryViaWasmdPrecompile()` is true), matching the existing restriction already applied to `MsgInternalEVMDelegateCall`.
2. Document and strongly recommend (or enforce at the precompile level) that any EVM contract calling `wasmd.execute()` against non-trusted CW contract addresses must not rely on state read before/after the call without an explicit reentrancy lock, and consider exposing a "no external re-entry" mode analogous to the swapper hardening recommended in the source report (verify balances via direct state reads rather than trusting call return values across the wasmd boundary).

### Proof of Concept
1. Deploy EVM contract `Caller` that: reads its own `usei`/token balance via the Bank precompile, calls `WASMD_PRECOMPILE_ADDRESS.execute(cwAttacker, msg, coins)`, then reads its balance again and asserts the delta matches an expected amount (mirroring a naive "check total shares before/after" pattern).
2. Deploy a CosmWasm contract `cwAttacker` whose `execute` handler, upon invocation, returns a `CallEVM` custom message targeting `Caller` (or a shared vault/pointer contract) that invokes a state-mutating function (e.g., calling back into `Caller`'s deposit/withdraw function, or directly calling the Bank precompile to move funds) before returning control to the wasmd precompile.
3. From an EOA, call `Caller`'s function that performs the `wasmd.execute()` call. Because `ctx.EVMEntryViaWasmdPrecompile()` is true for this call chain, `HandleInternalEVMCall`'s `CallEVM` guard does not block the nested EVM call, and `cwAttacker`'s `CallEVM` message successfully mutates state belonging to `Caller` (or the shared contract) before `Caller`'s post-call invariant check executes, demonstrating the reentrancy and resulting incorrect accounting/fund extraction.

### Citations

**File:** x/evm/keeper/evm.go (L30-45)
```go
func (k *Keeper) HandleInternalEVMCall(ctx sdk.Context, req *types.MsgInternalEVMCall) (*sdk.Result, error) {
	var to *common.Address
	if req.To != "" {
		addr := common.HexToAddress(req.To)
		to = &addr
	}
	senderAddr, err := sdk.AccAddressFromBech32(req.Sender)
	if err != nil {
		return nil, err
	}
	ret, err := k.CallEVM(ctx, k.GetEVMAddressOrDefault(ctx, senderAddr), to, req.Value, req.Data)
	if err != nil {
		return nil, err
	}
	return &sdk.Result{Data: ret}, nil
}
```

**File:** x/evm/keeper/evm.go (L79-120)
```go
func (k *Keeper) CallEVM(ctx sdk.Context, from common.Address, to *common.Address, val *sdk.Int, data []byte) (retdata []byte, reterr error) {
	if ctx.IsEVM() && !ctx.EVMEntryViaWasmdPrecompile() {
		return nil, errors.New("sei does not support EVM->CW->EVM call pattern")
	}
	if to == nil && len(data) > params.MaxInitCodeSize {
		return nil, fmt.Errorf("%w: code size %v, limit %v", core.ErrMaxInitCodeSizeExceeded, len(data), params.MaxInitCodeSize)
	}
	if to != nil && to.Cmp(common.HexToAddress(solo.SoloAddress)) == 0 {
		return nil, errors.New("cannot call Solo precompile via CosmWasm")
	}
	value := utils.Big0
	if val != nil {
		if val.IsNegative() {
			return nil, sdkerrors.ErrInvalidCoins
		}
		value = val.BigInt()
	}
	// This call was not part of an existing StateTransition, so it should trigger one
	executionCtx := ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx)).WithEVMEntryViaWasmdPrecompile(false)
	stateDB := state.NewDBImpl(executionCtx, k, false)
	gp := k.GetGasPool()
	evmMsg := &core.Message{
		Nonce:     stateDB.GetNonce(from), // replay attack is prevented by the AccountSequence number set on the CW transaction that triggered this call
		GasLimit:  k.getEvmGasLimitFromCtx(ctx),
		GasPrice:  utils.Big0, // fees are already paid on the CW transaction
		GasFeeCap: utils.Big0,
		GasTipCap: utils.Big0,
		To:        to,
		Value:     value,
		Data:      data,
		From:      from,
	}
	// should not increment nonce since this isn't a transaction
	res, err := k.applyEVMMessage(ctx, evmMsg, stateDB, gp, false)
	if err != nil {
		return nil, err
	}
	k.consumeEvmGas(ctx, res.UsedGas)
	if res.Err != nil {
		return nil, res.Err
	}
	surplus, err := stateDB.Finalize()
```

**File:** precompiles/wasmd/wasmd.go (L206-293)
```go
func (p PrecompileExecutor) execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()
	if readOnly {
		rerr = errors.New("cannot call execute from staticcall")
		return
	}
	if err := pcommon.ValidateArgsLength(args, 3); err != nil {
		rerr = err
		return
	}

	// type assertion will always succeed because it's already validated in p.Prepare call in Run()
	contractAddrStr := args[0].(string)
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		erc20pointer, _, erc20exists := p.evmKeeper.GetERC20CW20Pointer(ctx, contractAddrStr)
		erc721pointer, _, erc721exists := p.evmKeeper.GetERC721CW721Pointer(ctx, contractAddrStr)
		erc1155pointer, _, erc1155exists := p.evmKeeper.GetERC1155CW1155Pointer(ctx, contractAddrStr)
		if (!erc20exists || erc20pointer.Cmp(callingContract) != 0) && (!erc721exists || erc721pointer.Cmp(callingContract) != 0) && (!erc1155exists || erc1155pointer.Cmp(callingContract) != 0) {
			return nil, 0, fmt.Errorf("%s is not a pointer of %s", callingContract.Hex(), contractAddrStr)
		}
	}
	// addresses will be sent in Sei format
	contractAddr, err := sdk.AccAddressFromBech32(contractAddrStr)
	if err != nil {
		rerr = err
		return
	}
	senderAddr, found := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !found {
		rerr = types.NewAssociationMissingErr(caller.Hex())
		return
	}
	msg := args[1].([]byte)
	coins := sdk.NewCoins()
	coinsBz := args[2].([]byte)
	if err := json.Unmarshal(coinsBz, &coins); err != nil {
		rerr = err
		return
	}
	coinsValue := coins.AmountOf(sdk.MustGetBaseDenom()).Mul(state.SdkUseiToSweiMultiplier).BigInt()
	if (value == nil && coinsValue.Sign() == 1) || (value != nil && coinsValue.Cmp(value) != 0) {
		rerr = errors.New("coin amount must equal value specified")
		return
	}

	// Run basic validation, can also just expose validateLabel and validate validateWasmCode in sei-wasmd
	msgExecute := wasmtypes.MsgExecuteContract{
		Sender:   senderAddr.String(),
		Contract: contractAddr.String(),
		Msg:      msg,
		Funds:    coins,
	}

	if err := msgExecute.ValidateBasic(); err != nil {
		rerr = err
		return
	}

	useiAmt := coins.AmountOf(sdk.MustGetBaseDenom())
	if value != nil && !useiAmt.IsZero() {
		useiAmtAsWei := useiAmt.Mul(state.SdkUseiToSweiMultiplier).BigInt()
		coin, err := pcommon.HandlePaymentUsei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderAddr, useiAmtAsWei, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
		if err != nil {
			rerr = err
			return
		}
		// sanity check coin amounts match
		if !coin.Amount.Equal(useiAmt) {
			rerr = errors.New("mismatch between coins and payment value")
			return
		}
	}
	res, err := p.wasmdKeeper.Execute(ctx, contractAddr, senderAddr, msg, coins)
	if err != nil {
		rerr = err
		return
	}
	ret, rerr = method.Outputs.Pack(res)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** x/evm/client/wasm/encoder.go (L14-30)
```go
func EncodeCallEVM(rawMsg json.RawMessage, sender sdk.AccAddress, info wasmvmtypes.MessageInfo) ([]sdk.Msg, error) {
	encodedCallEVM := bindings.CallEVM{}
	if err := json.Unmarshal(rawMsg, &encodedCallEVM); err != nil {
		return []sdk.Msg{}, err
	}
	decodedData, err := base64.StdEncoding.DecodeString(encodedCallEVM.Data)
	if err != nil {
		return []sdk.Msg{}, err
	}
	internalCallEVMMsg := types.MsgInternalEVMCall{
		Sender: sender.String(),
		To:     encodedCallEVM.To,
		Value:  encodedCallEVM.Value,
		Data:   decodedData,
	}
	return []sdk.Msg{&internalCallEVMMsg}, nil
}
```

**File:** wasmbinding/message_plugin.go (L19-32)
```go
func (r *CustomRouter) Handler(msg sdk.Msg) baseapp.MsgServiceHandler {
	switch m := msg.(type) {
	case *evmtypes.MsgInternalEVMCall:
		return func(ctx sdk.Context, _ sdk.Msg) (*sdk.Result, error) {
			return r.evmKeeper.HandleInternalEVMCall(ctx, m)
		}
	case *evmtypes.MsgInternalEVMDelegateCall:
		return func(ctx sdk.Context, _ sdk.Msg) (*sdk.Result, error) {
			return r.evmKeeper.HandleInternalEVMDelegateCall(ctx, m)
		}
	default:
		return r.MessageRouter.Handler(msg)
	}
}
```
