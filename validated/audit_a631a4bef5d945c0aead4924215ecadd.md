### Title
IBC-refunded coins are permanently locked in the `erc20` module account when auto-conversion to a fee-on-transfer/incompatible ERC20 fails after coins are already escrowed - (File: x/erc20/keeper/ibc_callbacks.go, x/erc20/keeper/msg_server.go)

### Summary
This is the Cosmos EVM analog of the Sherlock "router not compatible with fee-on-transfer tokens" finding. Instead of a Uniswap-style router failing to account for a fee taken during an ERC20 transfer, the `x/erc20` IBC middleware's automatic timeout/ack-failure reconversion path (`ConvertCoinToERC20FromPacket` → `ConvertCoinNativeERC20`) escrows the user's refunded bank coins into the module account *before* attempting the ERC20-side transfer, and only checks whether the correct amount actually arrived at the recipient afterward. If the ERC20 is a fee-on-transfer token (or otherwise delivers less than the requested amount / reverts post-transfer), the balance-invariance check fails, the function returns an error — but the caller (`ConvertCoinToERC20FromPacket`) swallows that error and returns `nil`, so the IBC ack/timeout message still succeeds and commits state. The already-escrowed coins are never burned and never returned to the user.

### Finding Description
`ConvertCoinNativeERC20` [1](#0-0)  performs, in order:
1. Reads the receiver's pre-transfer ERC20 balance.
2. Escrows the Cosmos coin from the sender into the `erc20` module account via `SendCoinsFromAccountToModule` [2](#0-1) .
3. Calls the ERC20 contract's `transfer` from the module to the receiver [3](#0-2) .
4. Compares the receiver's balance delta to the expected `amount`, returning `ErrBalanceInvariance` if they differ [4](#0-3) .
5. Only *after* that check passes does it burn the escrowed coins [5](#0-4) .

Because the escrow (step 2) happens unconditionally before the balance check (step 4) or even before the EVM call outcome is known, any failure path (fee-on-transfer token skimming part of the amount, a paused/blacklisting token, or an EVM call error) causes the function to `return err` with the coins already moved into the module account and never burned nor refunded.

This function is invoked from the IBC failure-recovery path `ConvertCoinToERC20FromPacket`, used both by `OnTimeoutPacket` and `OnAcknowledgementPacket` (on error acks) [6](#0-5) . Critically, the error from `ConvertCoinNativeERC20` is caught and discarded: [7](#0-6) 
The function then `return nil` at line 256, meaning `OnTimeoutPacket`/`OnAcknowledgementPacket` report success to the IBC core even though a state mutation (coin escrow without corresponding burn/refund) was left half-applied and committed. The code comment explicitly claims "the user receives the corresponding bank token from the TokenPair instead" [8](#0-7) , but that is false given the actual escrow-then-fail ordering: the bank coin was already moved out of the user's account into the module account by the time the failure is detected.

The same unconditional-escrow-then-check pattern also exists in `convertERC20IntoCoinsForNativeToken` (used by the user-facing `MsgConvertERC20`) [9](#0-8) , but that path is a top-level `Msg` handler, so an error return there correctly triggers Cosmos SDK's message-level state rollback (CacheContext discard on non-nil error). The IBC callback path is different: because `ConvertCoinToERC20FromPacket` intentionally swallows the inner error and returns `nil`, the normal SDK rollback-on-error protection is bypassed, and the partially-applied state is persisted.

The presence of test contracts `ERC20DirectBalanceManipulation.sol` (skims funds to a third party during transfer) and `ERC20MaliciousDelayed.sol` [10](#0-9)  confirms the team is aware that ERC20 tokens registered via governance can behave adversarially/non-standard on `transfer`, which is exactly the trigger condition here.

### Impact Explanation
Any token pair backed by a native ERC20 contract that is (or later becomes, e.g. via a proxy upgrade) fee-on-transfer, pausable, blacklisting, or otherwise delivers less than the exact requested amount will cause `ConvertCoinNativeERC20` to fail during IBC timeout/ack-error auto-reconversion. Every time this occurs:
- The user's coin (which should have been refunded to them by the IBC timeout/failed-ack flow) is instead re-escrowed into the `erc20` module account and never burned nor returned — a Critical, irreversible loss/freezing of user funds.
- The module account accumulates un-backed, un-burned bank coin balance while the ERC20 total-supply/escrow accounting invariant that the whole `ConvertCoin`/`ConvertERC20` design relies on (`ErrBalanceInvariance` checks) is silently violated across the system, corrupting the 1:1 accounting between the native coin supply and the ERC20-side escrow that other code paths assume holds.
- Because the failure is swallowed and acknowledged as success to the IBC stack, there is no error surfaced to relayers/users indicating anything went wrong, making the loss silent and unrecoverable through any built-in retry mechanism.

This fits the "Critical permanent freezing/locking of user funds... or token-pair-backed balances" and "irreversible accounting corruption of spendable user value" impact categories.

### Likelihood Explanation
This is triggerable by any unprivileged party that owns/controls (or can influence via a standard governance-registrable ERC20) a fee-on-transfer or otherwise non-standard ERC20 token pair, combined with any ordinary IBC transfer that times out or receives an error acknowledgement — both are routine, permissionless occurrences in IBC operation, not requiring privileged access, malicious validators, or relayer collusion. The only precondition is that a native-ERC20 token pair for a non-standard ERC20 exists; governance-based `RegisterERC20` does not appear to enforce that only vanilla, fee-free ERC20 implementations are eligible (the test suite's malicious-token fixtures reinforce that this scenario is anticipated as reachable).

### Recommendation
In `ConvertCoinNativeERC20`, defer the coin escrow until after the ERC20-side transfer has been confirmed to deliver the exact expected amount, or perform the entire operation inside an explicit `ctx.CacheContext()` that is only written back on full success. Additionally, `ConvertCoinToERC20FromPacket` should not unconditionally swallow errors from `ConvertCoinNativeERC20` as a no-op success; if the conversion partially mutates state before failing, that mutation must be rolled back (e.g., via a cached context per attempt), so that on failure the user's original bank coin balance is genuinely left untouched/refunded as the code comments claim.

### Proof of Concept
1. Governance registers a native ERC20 token pair whose contract implements `transfer` with a fee (e.g., deducts 1% and sends it to a third address), similar to the repo's own `ERC20DirectBalanceManipulation.sol` test fixture.
2. A user converts some of this ERC20 to the native Cosmos coin via `MsgConvertERC20` (escrow succeeds, standard forward-direction flow, unaffected).
3. The user initiates an IBC transfer of that coin that ultimately times out or is acknowledged with an error (e.g., destination chain rejects it).
4. The IBC transfer module refunds the coin to the sender, then the `erc20` module's `OnTimeoutPacket`/`OnAcknowledgementPacket` hook attempts automatic reconversion via `ConvertCoinToERC20FromPacket` → `ConvertCoinNativeERC20`.
5. `ConvertCoinNativeERC20` escrows the refunded coin into the module account, calls `transfer` on the fee-on-transfer ERC20, the receiver gets less than `amount`, the balance-invariance check fails and returns `ErrBalanceInvariance`.
6. `ConvertCoinToERC20FromPacket` catches this error, emits a `EventTypeFailedConvertERC20` event, and returns `nil`.
7. The IBC ack/timeout message commits successfully. The user ends up with neither the original bank coin (it is now stuck in the `erc20` module account) nor a full ERC20 balance (they received `amount - fee`), while the escrowed coin is never burned — resulting in permanently locked/lost funds and an accounting mismatch between escrowed native coin and ERC20 supply.

### Citations

**File:** x/erc20/keeper/msg_server.go (L71-152)
```go
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

	// Check expected receiver balance after transfer
	balanceCoinAfter := k.bankKeeper.GetBalance(ctx, receiver, pair.Denom)
	expCoin := balanceCoin.Add(coins[0])

	if ok := balanceCoinAfter.Equal(expCoin); !ok {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid coin balance - expected: %v, actual: %v",
			expCoin, balanceCoinAfter,
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L237-306)
```go
func (k Keeper) ConvertCoinNativeERC20(
	ctx sdk.Context,
	pair types.TokenPair,
	amount math.Int,
	receiver common.Address,
	sender sdk.AccAddress,
) error {
	if !amount.IsPositive() {
		return sdkerrors.Wrap(types.ErrNegativeToken, "converted coin amount must be positive")
	}

	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()

	balanceToken := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceToken == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

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

	// Burn escrowed Coins
	err = k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins)
	if err != nil {
		return sdkerrors.Wrap(err, "failed to burn coins")
	}

	return nil
}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L164-188)
```go
func (k Keeper) OnAcknowledgementPacket(
	ctx sdk.Context, _ channeltypes.Packet,
	data transfertypes.FungibleTokenPacketData,
	ack channeltypes.Acknowledgement,
) error {
	switch ack.Response.(type) {
	case *channeltypes.Acknowledgement_Error:
		// convert the token from Cosmos Coin to its ERC20 representation
		return k.ConvertCoinToERC20FromPacket(ctx, data)
	default:
		// the acknowledgement succeeded on the receiving chain so nothing needs to
		// be executed and no error needs to be returned
		return nil
	}
}

// OnTimeoutPacket converts the IBC coin to ERC20 after refunding the sender
// since the original packet sent was never received and has been timed out.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnTimeoutPacket still
// succeeds, but the user receives the corresponding bank token from the TokenPair
// instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, _ channeltypes.Packet, data transfertypes.FungibleTokenPacketData) error {
	return k.ConvertCoinToERC20FromPacket(ctx, data)
}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L236-253)
```go
		// Convert from Coin to ERC20
		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(sender), sender); err != nil {
			// We want to record only the failed attempt to reconvert the coins during IBC.
			defer func() {
				telemetry.IncrCounter(1, types.ModuleName, "ibc", "error", "total")
			}()
			ctx.EventManager().EmitEvents(
				sdk.Events{
					sdk.NewEvent(
						types.EventTypeFailedConvertERC20,
						sdk.NewAttribute(types.AttributeCoinSourceChannel, pair.Denom),
						sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
						sdk.NewAttribute("error", err.Error()),
					),
				},
			)
			return nil
		}
```

**File:** contracts/solidity/x/erc20/keeper/testdata/ERC20DirectBalanceManipulation.sol (L1-23)
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
