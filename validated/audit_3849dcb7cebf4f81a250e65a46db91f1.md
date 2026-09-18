### Title
Bank precompile `send()` bypasses `BlockedAddr`/`IsInDenomAllowList` checks enforced by `MsgSend`, allowing transfers to blocked or non-allow-listed addresses - (File: precompiles/bank/legacy/v580/bank.go)

### Summary
The Cosmos `x/bank` module enforces two access-control invariants on every native coin transfer submitted through `MsgSend`/`MsgMultiSend`: the sender and recipient must not be a `BlockedAddr`, and for `tokenfactory` (`factory/...`) denoms, both parties must be in the denom's `DenomAllowList` if one is configured. These checks are performed explicitly in the message handler before the state-changing `SendCoins`/`InputOutputCoins` call is made. The EVM `bank` precompile's `send` method, however, calls the underlying `bankKeeper.SendCoins` directly without ever performing these two checks, so any EVM transaction sender who is the registered pointer contract for a token-factory denom can move funds to or from a blocked/denylisted address, breaking the same "recipient/sender must not be restricted" invariant that the original report flags for `approve()`.

### Finding Description
`x/bank`'s standard entry point performs the sanity checks before mutating state: [1](#0-0) 

`BlockedAddr` and `IsInDenomAllowList` are the enforcement primitives: [2](#0-1) [3](#0-2) 

The EVM Bank precompile at address `0x0000000000000000000000000000000000001001` exposes a `send(fromAddress, toAddress, denom, amount)` method that is reachable from any EVM contract that has been registered as the ERC20-native pointer for a denom (e.g. `NativeSeiTokensERC20`/`contracts/src/NativeSeiTokensERC20.sol`). Its implementation calls `bankKeeper.SendCoins` **directly**, with no call to `BlockedAddr` or `IsInDenomAllowList`: [4](#0-3) 

This is structurally identical to the referenced report: the protocol has a well-defined "sanity" check (`transferSanity`/`BlockedAddr`+`IsInDenomAllowList`) that is applied on the primary/expected code path (`ERC20.transfer`/`MsgSend`) but omitted on an alternate code path (`approve`/precompile `send`) that reaches the same underlying state mutation.

### Impact Explanation
Because the bank precompile's `send` skips `BlockedAddr` and `IsInDenomAllowList`, any pointer contract (invoked by an ordinary EVM caller through `NativeSeiTokensERC20.transfer`/`transferFrom`, which delegate to `IBank.send`) can move `usei` or token-factory-denominated coins to or from addresses that governance/module logic has explicitly blocked (module accounts, the `evm_coinbase` reserved prefix) or that a token-factory admin has explicitly excluded via `SetDenomAllowList`. This breaks the documented bank invariant that blocked/denylisted addresses cannot send or receive funds, enabling unauthorized transfers via a precompile and, for allow-listed token-factory denoms specifically, a bypass of the issuer's access-control policy on the token — a direct discrepancy between the enforced invariant on the Cosmos message path and the EVM-reachable precompile path.

### Likelihood Explanation
The precompile is reachable by any unprivileged EVM transaction sender through a standard ERC20 pointer contract call (`transfer`/`transferFrom` on `NativeSeiTokensERC20`, which is the canonical bridge for native/token-factory denoms into the EVM). No special privilege beyond deploying/using the pointer's `send` path is required, so likelihood is high for any denom that has a `BlockedAddr` entry or a configured `DenomAllowList`.

### Recommendation
Add the same `BlockedAddr`/`IsInDenomAllowList` checks that `msgServer.Send` performs to `PrecompileExecutor.send` (and any other precompile or bridge path that calls `SendCoins`/`SendCoinsAndWei` directly) before invoking `bankKeeper.SendCoins`, or move the checks into `BaseSendKeeper.SendCoins` itself so all callers are protected uniformly regardless of entry point.

### Proof of Concept
1. A token-factory denom `factory/<admin>/foo` has a `DenomAllowList` configured via `SetDenomAllowList` restricting transfers to specific addresses (`sei-cosmos/x/bank/keeper/send.go` lines 478-484).
2. A native pointer contract `NativeSeiTokensERC20` is deployed for this denom (`contracts/src/NativeSeiTokensERC20.sol` lines 45-49), delegating `_update`/transfers to `IBank.send`.
3. Any account not in the allow list calls `NativeSeiTokensERC20.transfer(to, amount)`, which invokes `BankPrecompile.send(from, to, denom, amount)`.
4. `PrecompileExecutor.send` (`precompiles/bank/legacy/v580/bank.go` lines 121-159) calls `p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, ...)` without ever checking `IsInDenomAllowList` or `BlockedAddr`, so the transfer succeeds even though the equivalent `MsgSend` transaction would have been rejected with `"... is not allowed to send/receive funds"`.

### Citations

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-49)
```go
func (k msgServer) Send(goCtx context.Context, msg *types.MsgSend) (*types.MsgSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	if err := k.IsSendEnabledCoins(ctx, msg.Amount...); err != nil {
		return nil, err
	}

	from, err := sdk.AccAddressFromBech32(msg.FromAddress)
	if err != nil {
		return nil, err
	}
	to, err := sdk.AccAddressFromBech32(msg.ToAddress)
	if err != nil {
		return nil, err
	}

	allowListCache := make(map[string]AllowedAddresses)
	if !k.IsInDenomAllowList(ctx, from, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", msg.FromAddress)
	}

	if k.BlockedAddr(to) || !k.IsInDenomAllowList(ctx, to, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", msg.ToAddress)
	}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L372-381)
```go
// BlockedAddr checks if a given address is restricted from
// receiving funds.
func (k BaseSendKeeper) BlockedAddr(addr sdk.AccAddress) bool {
	if len(addr) == len(CoinbaseAddressPrefix)+8 {
		if bytes.Equal(CoinbaseAddressPrefix, addr[:len(CoinbaseAddressPrefix)]) {
			return true
		}
	}
	return k.blockedAddrs[addr.String()]
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L501-524)
```go
// IsInDenomAllowList checks if the given address is allowed to send the given coins.
// The check is performed only fot token factory denoms. For each token factory denom,
// it checks if there is allow list for the given denom. If there is no allow list,
// the address is allowed to send the coins. If there is an allow list, the address is
// allowed to send the coins only if it is in the allow list.
func (k BaseSendKeeper) IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool {
	for _, coin := range coins {
		// Skip if denom does not contain the token factory prefix
		if !strings.HasPrefix(coin.Denom, TokenFactoryPrefix) {
			continue
		}

		allowedAddresses := k.getAllowedAddresses(ctx, cache, coin.Denom)
		// skip if there is no allow list for the denom
		if len(allowedAddresses.set) == 0 {
			continue
		}

		if !allowedAddresses.contains(addr) {
			return false
		}
	}
	return true
}
```

**File:** precompiles/bank/legacy/v580/bank.go (L121-159)
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
