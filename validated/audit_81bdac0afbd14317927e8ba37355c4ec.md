Confirmed: `IsInDenomAllowList` and `BlockedAddr` checks live only in `msgServer.Send`/`MultiSend` [1](#0-0) , not inside `SendCoins`/`SendCoinsAndWei` in the base keeper. The bank precompile's `send` method now correctly routes through `bankMsgServer.Send` [2](#0-1) , but `sendNative` calls `p.bankKeeper.SendCoinsAndWei` directly, never invoking the msg server, `IsInDenomAllowList`, or `BlockedAddr` [3](#0-2) .

### Title
Tokenfactory Denom Allow-List and Blocked-Address Bypass via Bank Precompile `sendNative` - (File: precompiles/bank/bank.go)

### Summary
The bank precompile exposes two ways to move native/usei funds: `send` (ERC20-pointer-gated, routed through `bankMsgServer.Send`) and `sendNative` (plain usei/wei transfer triggered by attaching EVM `value` to a call). `sendNative` calls `p.bankKeeper.SendCoinsAndWei` directly instead of going through the bank module's `MsgServer.Send`, so it never runs `IsInDenomAllowList` or `BlockedAddr`, both of which are enforced only inside `msgServer.Send`/`MultiSend`.

### Finding Description
`sei-cosmos/x/bank/keeper/msg_server.go`'s `Send` handler enforces two authorization checks before calling the base-keeper `SendCoins`: the sender and receiver must be in the tokenfactory-configured denom allow-list (`IsInDenomAllowList`), and the receiver must not be a blocked address (`BlockedAddr`) [4](#0-3) . These checks are absent from the base keeper's `SendCoins`/`SendCoinsAndWei` implementations themselves — they are policy layered on top in the msg server, matching the `x/bank` docs which describe blocklisting as being enforced at "means such as `MsgSend`" [5](#0-4) .

The EVM bank precompile has two transaction methods. `send` (ERC20-native-pointer gated) was hardened to build a `banktypes.MsgSend` and dispatch it through `p.bankMsgServer.Send`, which re-enters the msg server and therefore the allow-list/blocked-address gate [2](#0-1) . `sendNative`, however — reachable from any EVM account by simply attaching `value` to a call to the bank precompile address — resolves usei/wei amounts and calls `p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei)` directly [3](#0-2) , bypassing both `IsInDenomAllowList` and `BlockedAddr`. This is the same "two entry points to the same underlying operation, only one of which re-runs the security policy" root cause pattern as the Strapi advisory (admin upload path enforced MIME policy, content API path called the service directly and skipped it).

### Impact Explanation
An account restricted from receiving or sending a tokenfactory denom via its `AllowList`, or an address administratively placed on the bank module's blocked-address list (typically module accounts, whose invariants can be broken if they unexpectedly receive funds — see `x/bank` docs warning that funds landing on a blocked address outside expected state-machine rules can halt the network) [6](#0-5) , can still receive or move `usei`/wei funds by routing the transfer through `sendNative` instead of `MsgSend`. This is an authority/permission-check bypass on a fund-movement path reachable by any unprivileged EVM sender, and can be used to funnel funds into module accounts that the state machine assumes are inaccessible to arbitrary senders, risking accounting invariant breaks.

### Likelihood Explanation
High likelihood of reachability: `sendNative` requires nothing but attaching value to a plain call on the well-known bank precompile address (`0x...1001`) from any associated EVM account — no special permission, pointer registration, or CosmWasm interaction is required [7](#0-6) . The only precondition for actual impact is that an operator has configured a denom allow-list or a blocked address the attacker wants to bypass — a config-dependent but standard, supported feature of `x/bank`/`x/tokenfactory`.

### Recommendation
Route `sendNative`'s fund movement through the bank `MsgServer.Send`/`SendCoinsAndWei`-equivalent path that re-applies `IsInDenomAllowList` and `BlockedAddr`, or factor those checks into the base keeper's `SendCoins`/`SendCoinsAndWei` so every caller (msg server, precompiles, wasm bindings) inherits them uniformly instead of relying on each call site to re-implement the policy.

### Proof of Concept
1. Operator configures a tokenfactory denom's `AllowList` to exclude address `B`, or marks `B` (e.g., a module account) as a `BlockedAddr`.
2. An attacker with an EVM account associated to sender address `A` calls the bank precompile (`0x0000000000000000000000000000000000001001`) method `sendNative(B)` with EVM `value` set to the desired usei amount.
3. `sendNative` resolves `A`→`senderSeiAddr`, `B`→`receiverSeiAddr`, and calls `p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei)` directly [8](#0-7) , without ever calling `IsInDenomAllowList` or `BlockedAddr`.
4. The transfer succeeds despite `B` being blocked/disallowed, whereas the equivalent `MsgSend` or the precompile's `send` method (via `bankMsgServer.Send`) would have been rejected.

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

**File:** precompiles/bank/bank.go (L232-248)
```go
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

	bz, err := method.Outputs.Pack(true)
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
```

**File:** precompiles/bank/bank.go (L251-264)
```go
func (p PrecompileExecutor) sendNative(ctx sdk.Context, method *abi.Method, args []interface{}, caller common.Address, callingContract common.Address, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call sendNative from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall sendNative")
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send")
	}

```

**File:** precompiles/bank/bank.go (L280-292)
```go
	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, 0, err
	}
	accExists := p.accountKeeper.HasAccount(ctx, receiverSeiAddr)
	if !accExists {
		defer metrics.RecordBankNewAccount(ctx.Context())
		p.accountKeeper.SetAccount(ctx, p.accountKeeper.NewAccountWithAddress(ctx, receiverSeiAddr))
	}
```

**File:** sei-cosmos/x/bank/spec/02_keepers.md (L15-25)
```markdown
## Blocklisting Addresses

The `x/bank` module accepts a map of addresses that are considered blocklisted
from directly and explicitly receiving funds through means such as `MsgSend` and
`MsgMultiSend` and direct API calls like `SendCoinsFromModuleToAccount`.

Typically, these addresses are module accounts. If these addresses receive funds
outside the expected rules of the state machine, invariants are likely to be
broken and could result in a halted network.

By providing the `x/bank` module with a blocklisted set of addresses, an error occurs for the operation if a user or client attempts to directly or indirectly send funds to a blocklisted account, for example, by using [IBC](http://docs.cosmos.network/master/ibc/).
```
