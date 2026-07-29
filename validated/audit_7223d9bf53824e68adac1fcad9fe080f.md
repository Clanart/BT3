## Analysis: Fee-on-Transfer/Deflationary ERC20 Analog in Cosmos EVM

The external report's bug class ("assume the transferred amount equals the sent amount without verifying actual delivered balance") has a concrete analog in this repository's native-ERC20 ⇄ Cosmos-coin conversion pipeline used by IBC. While the direct `ConvertERC20`/`ConvertCoin` message handlers *do* check pre/post balances and revert on mismatch [1](#0-0) , that balance check is not wrapped in an atomic (cached) context when invoked from the IBC receive-packet callback, allowing a partial state commit when a malicious deflationary token is used.

### Title
Deflationary/fee-on-transfer native ERC20 token pair causes non-atomic partial state commit in IBC `OnRecvPacket`, leading to duplicated/stuck value - (File: x/erc20/keeper/ibc_callbacks.go, x/erc20/keeper/msg_server.go, x/erc20/ibc_middleware.go)

### Summary
`RegisterERC20` is permissionless by default (`PermissionlessRegistration: true`) [2](#0-1) , so any user can register their own custom ERC20 contract as a "native ERC20" token pair (`OWNER_EXTERNAL`) [3](#0-2) . `ConvertCoinNativeERC20` escrows the bank coin from the caller **before** performing the real ERC20 `transfer` and verifying the resulting balance delta [4](#0-3) . If the registered ERC20 is deflationary/fee-on-transfer on its outgoing (module→user) transfer, the post-transfer balance check fails and the function returns `ErrBalanceInvariance` — but the escrow step (`SendCoinsFromAccountToModule`) has already been committed to the same, non-cached `ctx`.

This function is invoked directly from the IBC ICS20 receive-callback path, `x/erc20/keeper/ibc_callbacks.go`'s `OnRecvPacket`, which itself is called from `IBCMiddleware.OnRecvPacket` **after** the underlying ICS20 transfer module has already minted/unescrowed the bank coin to the receiver [5](#0-4) . Neither `IBCMiddleware.OnRecvPacket` nor `Keeper.OnRecvPacket` branches into a `ctx.CacheContext()` around the conversion attempt [6](#0-5) . Returning an error `Acknowledgement` (as opposed to a Go error/panic) is a *normal, successful* return from the callback's perspective in the IBC packet-handling machinery — it does not roll back interim state changes made earlier in the same call, it only causes an error acknowledgement to be written, which triggers a refund on the sending chain.

### Finding Description
1. Attacker deploys a custom ERC20 contract whose `transfer` function behaves normally when called by ordinary users, but silently under-delivers (keeps a "fee") specifically when `msg.sender == types.ModuleAddress` (i.e., outbound transfers *from* the erc20 module account to a user) — a pattern the codebase's own test fixtures acknowledge is realistic (`ERC20DirectBalanceManipulation`, `ERC20MaliciousDelayed`) [7](#0-6) .
2. Attacker permissionlessly registers this token via `MsgRegisterERC20` [8](#0-7) .
3. Attacker mints tokens to themselves and calls `ConvertERC20` (user→module direction, no fee triggered by design) to obtain 1:1 backed bank coins of the token-pair denom [9](#0-8) .
4. Attacker sends these bank coins out via IBC (`MsgTransfer`), escrowing them normally in the ICS20 escrow account.
5. When the packet is later processed by `OnRecvPacket` (e.g. receiving back on this chain, or via acknowledgement/timeout refund flow calling `ConvertCoinToERC20FromPacket`), the underlying ICS20 transfer first credits the bank coin to the receiver (this state commits normally, since it's a distinct successful sub-call). The erc20 middleware then calls `ConvertCoinNativeERC20`, which escrows the just-credited bank coin from the receiver into the module account, then calls the malicious ERC20's `transfer` (module→user, triggering the fee). The post-transfer balance check fails with `ErrBalanceInvariance`, and the function returns an error.
6. `Keeper.OnRecvPacket` converts this into `channeltypes.NewErrorAcknowledgement(err)` and returns it normally (no panic) [10](#0-9) . Because this is a normal Go return, not a panic, the ICS26 packet-handling layer commits all state mutations that occurred during the call — including the now-orphaned escrow of the bank coin into the erc20 module account (never burned, since the `BurnCoins` step is never reached) and the partial/fee-reduced ERC20 amount delivered to the attacker.
7. The written error acknowledgement is then relayed back to the source chain, which — per standard ICS20 semantics — refunds the original sender there.

Net effect: value is created that did not exist before — the sender is refunded on the source chain, the receiver keeps a partial amount of real ERC20 tokens, and the intermediate bank coin is permanently stuck (escrowed, unburned) in the erc20 module account. This is unauthorized duplication of spendable value across a native-balance/ERC20/IBC-escrow boundary.

### Impact Explanation
This matches the Critical impact bar: "unauthorized minting, burning, duplication, resurrection, or irreversible accounting corruption of spendable user value across native balances, EVM balances, ERC20 representations, IBC escrows, or precompile-mediated assets." The bug is triggerable by any unprivileged account (permissionless registration is the default parameter) using only ordinary `MsgRegisterERC20`, `MsgConvertERC20`, and `MsgTransfer` transactions plus a self-authored malicious ERC20 contract — no privileged keys, governance, or malicious relayer/validator assumptions are required.

### Likelihood Explanation
Likelihood is high in any deployment where `PermissionlessRegistration` remains at its default value of `true` [2](#0-1) , since the attacker fully controls the malicious contract's behavior and needs no cooperation from any other party. The attack is reproducible deterministically (not dependent on races or timing).

### Recommendation
Wrap the entire ICS20 receive-callback conversion path (`Keeper.OnRecvPacket` / `ConvertCoinNativeERC20`'s escrow+transfer+burn sequence) in a `ctx.CacheContext()` and only commit (`writeCache()`) when the whole conversion succeeds, mirroring the atomicity guarantee ADR-008 middleware is expected to provide. Additionally, treat the pre-existing balance-invariance check in `ConvertCoinNativeERC20`/`convertERC20IntoCoinsForNativeToken` as a hard precondition: reorder operations so that the ERC20-side transfer and its balance verification happen before the bank-side escrow is finalized, or perform the escrow and unescrow atomically within a single cached branch that is rolled back on any invariance failure. Consider also disabling `PermissionlessRegistration` by default, or explicitly disallowing conversion flows for tokens found to alter balances non-linearly (fee-on-transfer/rebasing), per the token integration checklist referenced in the source report.

### Proof of Concept
1. Deploy `MaliciousFeeERC20` (custom contract): `transfer(to, amount)` delivers `amount` in full when `msg.sender != erc20ModuleAddress`, but delivers `amount - 1` when `msg.sender == erc20ModuleAddress` (module-originated transfers), while still returning `true`.
2. Submit `MsgRegisterERC20` for this contract (permissionless, default params).
3. Mint tokens to self; submit `MsgConvertERC20` to escrow tokens into the module and receive 1:1 bank coins of `pair.Denom`.
4. Submit `MsgTransfer` to send these bank coins over IBC to a channel/receiver that will trigger `OnRecvPacket`/`OnAcknowledgementPacket`/`OnTimeoutPacket` processing back on this chain for the same denom (e.g. round-trip via a second hop, or force a timeout/error ack on the initial send so `ConvertCoinToERC20FromPacket` fires on this chain).
5. Observe: `ConvertCoinNativeERC20` escrows the bank coin from the receiver to the erc20 module account, then the ERC20 `transfer` under-delivers by 1, causing `ErrBalanceInvariance` and an error acknowledgement — while the escrow step remains committed in state (verify via `BankKeeper.GetBalance` on the erc20 module account showing the escrowed coin, and `BalanceOf` on the receiver showing partial ERC20 delivered), and the source chain independently refunds the sender due to the error ack.

### Citations

**File:** x/erc20/keeper/msg_server.go (L63-141)
```go
// convertERC20IntoCoinsForNativeToken handles the erc20 conversion for a native erc20 token
// pair:
//   - escrow tokens on module account
//   - mint coins on bank module
//   - send minted coins to the receiver
//   - check if coin balance increased by amount
//   - check if token balance decreased by amount
//   - check for unexpected `Approval` event in logs
func (k Keeper) convertERC20IntoCoinsForNativeToken(
	ctx sdk.Context,
	pair types.TokenPair,
	msg *types.MsgConvertERC20,
	receiver sdk.AccAddress,
	sender common.Address,
) (*types.MsgConvertERC20Response, error) {
	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()
	balanceCoin := k.bankKeeper.GetBalance(ctx, receiver, pair.Denom)
	balanceToken := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceToken == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	// Escrow tokens on module account
	transferData, err := erc20.Pack("transfer", types.ModuleAddress, msg.Amount.BigInt())
	if err != nil {
		return nil, err
	}

	res, err := k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)
	if err != nil {
		return nil, err
	}

	// Check evm call response
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return nil, err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return nil, err
		}
		if !unpackedRet.Value {
			return nil, sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute transfer")
		}
	}

	// Check expected escrow balance after transfer execution
	// NOTE: coin fields already validated in the ValidateBasic() of the message
	coins := sdk.Coins{sdk.Coin{Denom: pair.Denom, Amount: msg.Amount}}
	tokens := coins[0].Amount.BigInt()
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceTokenAfter == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}

	// Mint coins
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return nil, err
	}

	// Send minted coins to the receiver
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiver, coins); err != nil {
		return nil, err
	}

```

**File:** x/erc20/keeper/msg_server.go (L256-297)
```go
	// Escrow Coins on module account
	coins := sdk.Coins{{Denom: pair.Denom, Amount: amount}}
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, coins); err != nil {
		return sdkerrors.Wrap(err, "failed to escrow coins")
	}

	// Unescrow Tokens and send to receiver
	res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, true, nil, "transfer", receiver, amount.BigInt())
	if err != nil {
		return err
	}

	// Check unpackedRet execution
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return err
		}
		if !unpackedRet.Value {
			return sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute unescrow tokens from user")
		}
	}

	// Check expected Receiver balance after transfer execution
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceTokenAfter == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	exp := big.NewInt(0).Add(balanceToken, amount.BigInt())

	if r := balanceTokenAfter.Cmp(exp); r != 0 {
		return sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v", exp, balanceTokenAfter,
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L324-362)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}

		pair, err := k.registerERC20(ctx, common.HexToAddress(addr))
		if err != nil {
			return nil, err
		}

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeRegisterERC20,
				sdk.NewAttribute(types.AttributeKeyCosmosCoin, pair.Denom),
				sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
			),
		)
	}

	return &types.MsgRegisterERC20Response{}, nil
}
```

**File:** x/erc20/types/params.go (L24-28)
```go
func DefaultParams() Params {
	return Params{
		EnableErc20:                true,
		PermissionlessRegistration: true,
	}
```

**File:** x/erc20/keeper/proposals.go (L16-41)
```go
// RegisterERC20 creates a Cosmos coin and registers the token pair between the
// coin and the ERC20
func (k Keeper) registerERC20(
	ctx sdk.Context,
	contract common.Address,
) (*types.TokenPair, error) {
	// Check if ERC20 is already registered
	if k.IsERC20Registered(ctx, contract) {
		return nil, errorsmod.Wrapf(
			types.ErrTokenPairAlreadyExists, "token ERC20 contract already registered: %s", contract.String(),
		)
	}

	metadata, err := k.CreateCoinMetadata(ctx, contract)
	if err != nil {
		return nil, errorsmod.Wrap(
			err, "failed to create wrapped coin denom metadata for ERC20",
		)
	}

	pair := types.NewTokenPair(contract, metadata.Name, types.OWNER_EXTERNAL)
	err = k.SetToken(ctx, pair)
	if err != nil {
		return nil, err
	}
	return &pair, nil
```

**File:** x/erc20/ibc_middleware.go (L53-67)
```go
func (im IBCMiddleware) OnRecvPacket(
	ctx sdk.Context,
	channelVersion string,
	packet channeltypes.Packet,
	relayer sdk.AccAddress,
) exported.Acknowledgement {
	ack := im.Module.OnRecvPacket(ctx, channelVersion, packet, relayer)

	// return if the acknowledgement is an error ACK
	if !ack.Success() {
		return ack
	}

	return im.keeper.OnRecvPacket(ctx, packet, ack)
}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L118-140)
```go
	// Case 2. native ERC20 token
	case found && pair.IsNativeERC20():
		// Token pair is disabled -> return
		if !pair.Enabled {
			return ack
		}

		pair, err := k.MintingEnabled(ctx, recipient, coin.Denom)
		if err != nil {
			ctx.EventManager().EmitEvent(
				sdk.NewEvent("erc20_callback_failure",
					sdk.NewAttribute(types.TypeMsgConvertCoin, "mint_failure"),
					sdk.NewAttribute(types.AttributeKeyCosmosCoin, coin.Denom),
					sdk.NewAttribute(types.AttributeKeyReceiver, recipient.String()),
				),
			)
			return channeltypes.NewErrorAcknowledgement(err)
		}

		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(recipient.Bytes()), recipient); err != nil {
			return channeltypes.NewErrorAcknowledgement(err)
		}

```

**File:** x/erc20/keeper/testdata/ERC20DirectBalanceManipulation.sol (L1-23)
```text
// SPDX-License-Identifier: MIT

pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/presets/ERC20PresetMinterPauser.sol";

// This is an evil token. Whenever an A -> B transfer is called, half of the amount goes to B
// and half to a predefined C
contract ERC20DirectBalanceManipulation is ERC20PresetMinterPauser {
  address private _thief = 0x4dC6ac40Af078661fc43823086E1513635Eeab14;
  constructor(uint256 initialSupply)
    ERC20PresetMinterPauser("ERC20DirectBalanceManipulation", "ERC20DirectBalanceManipulation") {
      _setupRole(DEFAULT_ADMIN_ROLE, msg.sender);
      _mint(msg.sender, initialSupply);
  }
  function transfer(address recipient, uint256 amount) public virtual override returns (bool) {
    // Any time a transaction happens, the thief account siphons half.
    uint256 half = amount / 2;

    super.transfer(_thief, amount - half); // a - h for rounding
    return super.transfer(recipient, half);
  }
}
```
