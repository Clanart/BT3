## Analog Found

### Title
Tokenfactory denom-allowlist and module blocklist restrictions are bypassable via the EVM value-transfer / bank-precompile path - (File: `sei-cosmos/x/bank/keeper/send.go`)

### Summary
The Portainer bug is a classic "restriction enforced on one representation of an action, not on the functionally-equivalent one" bypass: `HostConfig.Binds` was checked but the equivalent `HostConfig.Mounts` was not. sei-chain has the same structural gap in `x/bank`: the denom `AllowList` restriction and the module-account `BlockedAddr` restriction are enforced only in the Cosmos `MsgSend`/`MsgMultiSend` message handlers, not in the underlying `SendCoins`/`SendCoinsAndWei` keeper primitives that those handlers call. The EVM native-transfer path and the `bank` precompile call those same lower-level primitives directly, so any user moving usei or tokenfactory-denom balances through an EVM transaction or the `bank` precompile's `send`/`sendNative` bypasses both restrictions entirely.

### Finding Description
`msgServer.Send` and `msgServer.MultiSend` enforce three checks before calling `SendCoins`/`InputOutputCoins`: `IsSendEnabledCoins`, `IsInDenomAllowList` (the tokenfactory-configurable per-denom allowlist), and `BlockedAddr` (the module-account/reserved-address blocklist): [1](#0-0) 

But `BaseSendKeeper.SendCoins`, the primitive these handlers call, performs no such checks at all — it only moves balances and lazily creates the recipient account: [2](#0-1) 

The EVM value-transfer path calls this same primitive (via `SendCoinsAndWei`) directly from the StateDB, with no allowlist or blocklist check in between: [3](#0-2) 

The `bank` precompile's `send` executor — reachable by any EVM contract or EOA holding an ERC20-native pointer — also calls `p.bankKeeper.SendCoins` directly, again with no `IsInDenomAllowList`/`BlockedAddr` check: [4](#0-3) 

This mirrors the Portainer root cause exactly: `x/bank`'s own documentation states that the blocklist is meant to prevent funds from reaching module accounts "outside the expected rules of the state machine," and that a breach "could result in a halted network": [5](#0-4) 

and that `AllowList` restrictions are supposed to be enforced on every send: [6](#0-5) 

Because the EVM/precompile path never goes through `msgServer.Send`/`MultiSend`, neither guarantee holds for value moved that way.

### Impact Explanation
- **Denom allowlist bypass (bank/tokenfactory authority):** A tokenfactory admin who sets `AllowList` on a denom (e.g., to satisfy compliance/KYC requirements, as exercised in `TestSendReceiverNotInAllowList`/`TestSendSenderNotInAllowList`) intends to block sends to/from unauthorized addresses. An EVM caller can move the same denom balance via the `bank` precompile's `send` or via a native EVM value transfer against an ERC20-native pointer to a restricted address, because `SendCoins` never calls `IsInDenomAllowList`. This is an unauthorized transfer via precompile that defeats an authority-configured restriction.
- **Module blocklist bypass (invariant risk):** `BlockedAddr` exists specifically to stop funds from landing in module accounts outside the state machine's expected rules; the bank spec calls a breach of this invariant a potential cause of a halted network. Since EVM native transfers resolve the recipient's Sei address via `GetSeiAddressOrDefault` and call `SendCoinsAndWei` with no `BlockedAddr` check, an EVM transaction whose `to` address collides with (or is deliberately crafted to map to) a module account's byte representation can push funds into that module account outside of any expected accounting path.

### Likelihood Explanation
Both paths (EVM native value transfer and the `bank` precompile) are directly reachable by any unprivileged EVM transaction sender or contract on mainnet with no special privilege — exactly the "unprivileged transaction sender" and "public-RPC client" reach required. No governance, validator, or operator action is needed; the gap is structural in the keeper layering and applies to every tokenfactory denom that sets an `AllowList` and to every account protected by `BlockedAddr`.

### Recommendation
Move the `IsSendEnabledCoins`, `IsInDenomAllowList`, and `BlockedAddr` checks down into `BaseSendKeeper.SendCoins` (and `SendCoinsAndWei`/`InputOutputCoins`) so that every caller — `msgServer.Send`/`MultiSend`, the EVM `StateDB.send`, and the `bank` precompile — is subject to the same restriction, mirroring how the Portainer fix added the missing check to the shared/parallel enforcement point (Swarm's `TaskTemplate.ContainerSpec.Mounts` check) rather than only to the message-specific handler.

### Proof of Concept
1. Tokenfactory admin creates denom `factory/{admin}/kyc` and calls `SetDenomAllowList` restricting transfers to an approved address set (per `TestSendReceiverNotInAllowList`, `sei-cosmos/x/bank/app_test.go:117-149`).
2. A user not in the allowlist obtains this denom (e.g., pre-funded) and, instead of broadcasting a Cosmos `MsgSend` (which would fail with "is not allowed to receive/send funds"), calls the `bank` precompile's `send(from, to, "factory/{admin}/kyc", amount)` from an EVM transaction, or performs a native value transfer through an ERC20-native pointer for that denom.
3. Because `precompiles/bank/*/bank.go`'s `send` and `x/evm/state/balance.go`'s `send` both call `BankKeeper.SendCoins`/`SendCoinsAndWei` directly (`sei-cosmos/x/bank/keeper/send.go:164-182`), the transfer succeeds despite the sender/receiver not being on the denom's `AllowList`, and despite the recipient potentially being a `BlockedAddr` module account — both restrictions that would have stopped an equivalent `MsgSend`.

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

**File:** sei-cosmos/x/bank/keeper/send.go (L162-182)
```go
// SendCoins transfers amt coins from a sending account to a receiving account.
// An error is returned upon failure.
func (k BaseSendKeeper) SendCoins(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error {
	if err := k.SendCoinsWithoutAccCreation(ctx, fromAddr, toAddr, amt); err != nil {
		return err
	}

	// Create account if recipient does not exist.
	//
	// NOTE: This should ultimately be removed in favor a more flexible approach
	// such as delegated fee messages.
	accExists := k.ak.HasAccount(ctx, toAddr)
	if !accExists {
		defer func() {
			recordNewAccounts(ctx.Context(), 1)
		}()
		k.ak.SetAccount(ctx, k.ak.NewAccountWithAddress(ctx, toAddr))
	}

	return nil
}
```

**File:** x/evm/state/balance.go (L145-162)
```go
}

func (s *DBImpl) getSeiAddress(evmAddr common.Address) sdk.AccAddress {
	if s.coinbaseEvmAddress.Cmp(evmAddr) == 0 {
		return s.coinbaseAddress
	}
	return s.k.GetSeiAddressOrDefault(s.ctx, evmAddr)
}

func (s *DBImpl) send(from sdk.AccAddress, to sdk.AccAddress, amt *big.Int) {
	usei, wei := SplitUseiWeiAmount(amt)
	err := s.k.BankKeeper().SendCoinsAndWei(s.ctx, from, to, usei, wei)
	if err != nil {
		s.err = err
	}
}


```

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

**File:** sei-cosmos/x/bank/spec/03_messages.md (L12-26)
```markdown
The message will fail under the following conditions:

- The coins do not have sending enabled
- The `to` address is restricted

## MsgMultiSend

Send coins from and to a series of different address. If any of the receiving addresses do not correspond to an existing account, a new account is created.
+++ https://github.com/cosmos/cosmos-sdk/blob/v0.40.0/proto/cosmos/bank/v1beta1/tx.proto#L33-L39

The message will fail under the following conditions:

- Any of the coins do not have sending enabled
- Any of the `to` addresses are restricted
- Any of the coins are locked
```
