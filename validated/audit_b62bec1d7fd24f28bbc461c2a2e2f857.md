### Title
`sendNative()` in the Bank precompile bypasses `BlockedAddr` destination restrictions enforced by `MsgSend` - (File: `precompiles/bank/bank.go`)

### Summary
The Cosmos-side `MsgSend` handler enforces a destination restriction (`BlockedAddr`) before moving funds, but the EVM `sendNative` precompile method transfers `usei`/`wei` by calling the low-level keeper function directly, skipping that same restriction. This is the same bug class as the reported Stablecoin issue: one transfer entry point enforces the freeze/restriction on the destination, while a second, equally reachable entry point that moves the exact same underlying balance does not, allowing the restriction to be silently bypassed.

### Finding Description
The bank module's `MsgSend` handler explicitly checks the destination address against the blocked-address set before calling `SendCoins`: [1](#0-0) 

`BlockedAddr` is documented as protecting module accounts from receiving funds outside the expected rules of the state machine, precisely because doing so can break invariants and halt the network: [2](#0-1) 

The check itself lives in `BaseSendKeeper.BlockedAddr` and is not embedded inside `SendCoins`/`SendCoinsAndWei` — it is the *caller's* responsibility to invoke it: [3](#0-2) 

The Bank precompile's `send` method (token-factory/ERC20-pointer transfers) correctly goes through the guarded path, calling `p.bankMsgServer.Send(...)`, which performs the `BlockedAddr` and `IsInDenomAllowList` checks on both sides of the transfer: [4](#0-3) 

However, the `sendNative` method — reachable by any EVM caller sending native `value` to the precompile address `0x1001` — moves funds by calling `p.bankKeeper.SendCoinsAndWei` directly, with no `BlockedAddr` check on `receiverSeiAddr` anywhere in the function: [5](#0-4) 

Because `SendCoinsAndWei` is a low-level `BaseSendKeeper` method that does not itself enforce `BlockedAddr` (as shown above, that enforcement lives only in `msg_server.go`'s `Send`), any account — including module accounts that are deliberately placed in the blocked-address set to protect invariants — can receive `usei` sent through this EVM path, even though the equivalent Cosmos-side `MsgSend` to the same address would be rejected.

### Impact Explanation
This breaks the guarantee described in the bank module's own spec: blocked/module addresses are supposed to be unreachable via direct or indirect transfers precisely because unexpected credits to them can corrupt supply/module-account invariants and potentially halt the chain. An unprivileged EVM transaction sender can use `sendNative` to push `usei` into any blocked address (e.g. module accounts) that the "front door" (`MsgSend`) refuses, creating an inconsistency between the two equally reachable transfer surfaces. Depending on which module account is targeted, this can corrupt internal accounting invariants that the chain's modules assume are protected, which is the kind of state-machine invariant violation the bank spec explicitly warns can halt the network.

### Likelihood Explanation
High reachability: `sendNative` is a public, non-privileged method on the Bank precompile at a well-known address, callable by any EVM transaction with a nonzero `value` field and an already-associated sender address. No special role or governance action is required — a single transaction suffices.

### Recommendation
Add a `BlockedAddr` (and, if intended for consistency, `IsInDenomAllowList`) check on `receiverSeiAddr` inside `sendNative` before calling `SendCoinsAndWei`, mirroring the check already performed in `msg_server.go`'s `Send` handler. More robustly, move the `BlockedAddr` check into `BaseSendKeeper.SendCoins`/`SendCoinsAndWei` themselves so all current and future callers (precompiles, wasm bindings, module code) inherit the protection instead of relying on each caller to remember to add it.

### Proof of Concept
1. Identify (or have the chain designate) a blocked/module address `M` in the bank keeper's `blockedAddrs` map.
2. From an EVM account with an already-associated Sei address, call `MsgSend` targeting `M` — this fails with `"%s is not allowed to receive funds"` due to `k.BlockedAddr(to)` in `msg_server.go`.
3. From the same EVM account, call the Bank precompile's `sendNative(M)` with `value` set to a nonzero wei amount, targeting `M`'s associated address.
4. The call succeeds: `p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, M, usei, wei)` executes without any `BlockedAddr` check, crediting `M` with `usei`, whereas the equivalent Cosmos-side transfer to `M` was rejected.

### Citations

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L42-49)
```go
	allowListCache := make(map[string]AllowedAddresses)
	if !k.IsInDenomAllowList(ctx, from, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", msg.FromAddress)
	}

	if k.BlockedAddr(to) || !k.IsInDenomAllowList(ctx, to, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", msg.ToAddress)
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

**File:** precompiles/bank/bank.go (L232-245)
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
```

**File:** precompiles/bank/bank.go (L251-292)
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

	senderSeiAddr, ok := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !ok {
		return nil, 0, errors.New("invalid addr")
	}

	receiverAddr, ok := (args[0]).(string)
	if !ok || receiverAddr == "" {
		return nil, 0, errors.New("invalid addr")
	}

	receiverSeiAddr, err := sdk.AccAddressFromBech32(receiverAddr)
	if err != nil {
		return nil, 0, err
	}

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
