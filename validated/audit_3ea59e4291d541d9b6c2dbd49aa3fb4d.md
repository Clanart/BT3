### Title
Front-runnable `MsgClaim`/`MsgClaimSpecific` signature has no registered message handler, letting an attacker permanently burn a claimant's signed claim via the ante pipeline before the intended `solo` precompile call lands - ([File: precompiles/solo/solo.go])

### Summary
The `solo` precompile's `claim`/`claimSpecific` methods take a fully-formed, pre-signed Cosmos SDK transaction (raw bytes) as calldata, decode it, and manually re-implement signature/sequence verification inside `sigverify` before moving the claimant's native-Sei balance to their EVM-associated address [1](#0-0) . That embedded transaction is a standalone, independently valid `MsgClaim`/`MsgClaimSpecific` SDK message that is registered in the global interface registry and amino codec [2](#0-1) , but **no message route or gRPC service method exists for it** — `x/evm`'s `service Msg` only exposes `EVMTransaction`, `Send`, `RegisterPointer`, `AssociateContractAddress`, and `Associate` [3](#0-2) , and the legacy `Handler` switch has no `MsgClaim`/`MsgClaimSpecific` case either [4](#0-3) .

### Finding Description
Because `MsgClaim` is a registered `sdk.Msg` with `ValidateBasic`/`GetSigners` implemented [5](#0-4) , an attacker who observes the pending precompile call in the mempool (its calldata contains the claimant's raw signed tx bytes in plaintext) can extract that payload and broadcast it directly as an ordinary top-level Cosmos transaction, exactly as the "front-run the signed permit" step in the reported analog. That standalone transaction:
1. Passes through the standard `x/auth` ante pipeline (`SigVerificationDecorator` + `IncrementSequenceDecorator`), which verifies the signature and **bumps the claimant account's sequence number**, independent of whether any msg handler exists [6](#0-5) .
2. Then fails at message dispatch, because there is no registered route/service handler for `MsgClaim`/`MsgClaimSpecific` (only the five messages in the `service Msg` definition are wired up) [3](#0-2) [7](#0-6) .

In Cosmos SDK's standard `runTx` flow, the ante-handler cache is written (committing the sequence bump and fee deduction) before `runMsgs` executes; a failure in `runMsgs` does not roll back the ante-stage state changes. This means the attacker's junk transaction permanently consumes the claimant's specific pre-signed authorization (same effect as the Permit2 replay-protection revert in the original report) **without performing any transfer**, since no handler exists to route the message.

When the claimant's original EVM transaction calling `solo.claim()`/`solo.claimSpecific()` (containing the same signed tx bytes) is subsequently mined, `sigverify` will now reject it: `sig.Sequence != acct.GetSequence()` fails with `"account sequence mismatch for claim tx"` [8](#0-7) , exactly mirroring the reported bug class — the on-chain accounting update (`bankKeeper.SendCoins` to the claimer's EVM-linked address) never happens, while the authorization is irrecoverably spent.

### Impact Explanation
The claimant's native Sei balance remains stuck in their (unassociated or association-pending) Sei account and cannot be swept to their EVM address using that signed authorization; a new claim message must be signed with the bumped sequence, and the attacker can repeat this front-run against every subsequent attempt as long as they continue observing the mempool, indefinitely blocking a legitimate user's claim flow. This is a fund-availability/freezing impact directly analogous to the referenced Sherlock finding (signature consumed via an alternate path, target's balance not updated, target's original transaction reverts/fails).

### Likelihood Explanation
The `solo` precompile deliberately embeds a full raw signed Cosmos tx in EVM calldata, which is visible to anyone monitoring the mempool [1](#0-0) . No special privilege is needed to extract and rebroadcast it — any public RPC client can submit a bare `MsgClaim` transaction. The only requirement is that `MsgClaim`/`MsgClaimSpecific` remain routable as first-class SDK messages in the registry while lacking any handler, which is exactly the current state of the code.

### Recommendation
- Ensure `MsgClaim`/`MsgClaimSpecific` cannot be admitted as top-level, independently-broadcastable transactions — e.g., reject them in `ValidateBasic`/ante for direct submission, or route them through a handler that errors *before* the sequence increment is durably committed.
- Alternatively, decouple the claim signature from the account's live transaction sequence (e.g., use a dedicated nonce/claim-id namespace checked only by the `solo` precompile) so that a front-running broadcast of the extracted bytes cannot consume the general-purpose account sequence.
- Consider registering an explicit no-op/error handler for `MsgClaim`/`MsgClaimSpecific` in the msg router that fails validation prior to any ante-stage state mutation being retained, or reject decoding of txs containing these message types outside the `solo` precompile path entirely.

### Proof of Concept
1. Claimant signs a `MsgClaim{Sender: claimeeSeiAddr, Claimer: claimerEvmAddr}` tx (sequence N) and submits an EVM tx to the `solo` precompile's `claim(bytes)` method with the encoded tx as calldata.
2. Attacker observes this pending EVM tx in the mempool, decodes the calldata, and extracts the raw signed Cosmos tx bytes.
3. Attacker rebroadcasts those exact bytes as a standalone Cosmos transaction (not via the EVM/precompile path) with higher priority/gas so it lands first.
4. Ante handlers (`SigVerificationDecorator`, `IncrementSequenceDecorator`) validate and commit the sequence bump to `N+1` for the claimant's account [6](#0-5) ; message dispatch then fails since no route exists for `MsgClaim`.
5. The claimant's original `solo.claim()` EVM transaction is now mined; `sigverify` compares `sig.Sequence == N` against `acct.GetSequence() == N+1` and returns `"account sequence mismatch for claim tx"` [8](#0-7) , so `bankKeeper.SendCoins` is never invoked and the claimant's funds remain unmoved while their specific pre-signed authorization is spent.

Note: I was unable to fully trace the exact `baseapp.runTx` write-ordering (ante cache commit vs. `runMsgs` cache) in this indexed snapshot to give line-level confirmation of "ante state persists even if msg dispatch fails" — this is standard, well-documented Cosmos SDK behavior, but if you need it verified against this exact fork's `baseapp.go`, a full-repository Devin session would be able to inspect `sei-cosmos/baseapp/baseapp.go` in its entirety.

### Citations

**File:** precompiles/solo/solo.go (L230-259)
```go
func (p PrecompileExecutor) validate(ctx sdk.Context, caller common.Address, args []interface{}, readOnly bool) (claimMsg, sdk.AccAddress, error) {
	if readOnly {
		return nil, nil, errors.New("cannot call send from staticcall")
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, nil, err
	}
	tx, err := p.txConfig.TxDecoder()(args[0].([]byte))
	if err != nil {
		return nil, nil, fmt.Errorf("failed to decode claim tx due to %w", err)
	}
	if len(tx.GetMsgs()) != 1 {
		return nil, nil, fmt.Errorf("claim tx must contain exactly 1 message but %d were found", len(tx.GetMsgs()))
	}
	claimMsg, ok := tx.GetMsgs()[0].(claimMsg)
	if !ok {
		return nil, nil, errors.New("claim tx can only contain MsgClaim or MsgClaimSpecific type")
	}
	if common.HexToAddress(claimMsg.GetClaimer()).Cmp(caller) != 0 {
		return nil, nil, fmt.Errorf("claim tx is meant for %s but was sent by %s", claimMsg.GetClaimer(), caller.Hex())
	}
	sender, err := sdk.AccAddressFromBech32(claimMsg.GetSender())
	if err != nil {
		return nil, nil, fmt.Errorf("failed to parse claim tx sender due to %s", err)
	}
	if err := p.sigverify(ctx, tx, claimMsg, sender); err != nil {
		return nil, nil, err
	}
	return claimMsg, sender, nil
}
```

**File:** precompiles/solo/solo.go (L292-298)
```go
	if len(sigs) != 1 {
		return fmt.Errorf("claim tx should have exactly 1 signature but got %d", len(sigs))
	}
	sig := sigs[0]
	if sig.Sequence != acct.GetSequence() {
		return fmt.Errorf("account sequence mismatch for claim tx (%d vs. %d)", sig.Sequence, acct.GetSequence())
	}
```

**File:** x/evm/types/codec.go (L34-64)
```go
func RegisterCodec(cdc *codec.LegacyAmino) {
	cdc.RegisterConcrete(&MsgAssociate{}, "evm/MsgAssociate", nil)
	cdc.RegisterConcrete(&MsgEVMTransaction{}, "evm/MsgEVMTransaction", nil)
	cdc.RegisterConcrete(&MsgSend{}, "evm/MsgSend", nil)
	cdc.RegisterConcrete(&MsgRegisterPointer{}, "evm/MsgRegisterPointer", nil)
	cdc.RegisterConcrete(&MsgAssociateContractAddress{}, "evm/MsgAssociateContractAddress", nil)
	cdc.RegisterConcrete(&MsgClaim{}, "evm/MsgClaim", nil)
	cdc.RegisterConcrete(&MsgClaimSpecific{}, "evm/MsgClaimSpecific", nil)
}

func RegisterInterfaces(registry codectypes.InterfaceRegistry) {
	registry.RegisterImplementations((*govtypes.Content)(nil),
		&AddERCNativePointerProposal{},
		&AddERCCW20PointerProposal{},
		&AddERCCW721PointerProposal{},
		&AddERCCW1155PointerProposal{},
		&AddCWERC20PointerProposal{},
		&AddCWERC721PointerProposal{},
		&AddCWERC1155PointerProposal{},
		&AddERCNativePointerProposalV2{},
	)
	registry.RegisterImplementations(
		(*sdk.Msg)(nil),
		&MsgEVMTransaction{},
		&MsgSend{},
		&MsgRegisterPointer{},
		&MsgAssociateContractAddress{},
		&MsgClaim{},
		&MsgClaimSpecific{},
		&MsgAssociate{},
	)
```

**File:** proto/evm/tx.proto (L12-18)
```text
service Msg {
  rpc EVMTransaction(MsgEVMTransaction) returns (MsgEVMTransactionResponse);
  rpc Send(MsgSend) returns (MsgSendResponse);
  rpc RegisterPointer(MsgRegisterPointer) returns (MsgRegisterPointerResponse);
  rpc AssociateContractAddress(MsgAssociateContractAddress) returns (MsgAssociateContractAddressResponse);
  rpc Associate(MsgAssociate) returns (MsgAssociateResponse);
}
```

**File:** x/evm/handler.go (L14-38)
```go
func NewHandler(k *keeper.Keeper) sdk.Handler {
	msgServer := keeper.NewMsgServerImpl(k)

	return func(ctx sdk.Context, msg sdk.Msg) (*sdk.Result, error) {
		ctx = ctx.WithEventManager(sdk.NewEventManager())

		switch msg := msg.(type) {
		case *types.MsgEVMTransaction:
			res, err := msgServer.EVMTransaction(sdk.WrapSDKContext(ctx), msg)
			return sdk.WrapServiceResult(ctx, res, err)
		case *types.MsgSend:
			res, err := msgServer.Send(sdk.WrapSDKContext(ctx), msg)
			return sdk.WrapServiceResult(ctx, res, err)
		case *types.MsgRegisterPointer:
			res, err := msgServer.RegisterPointer(sdk.WrapSDKContext(ctx), msg)
			return sdk.WrapServiceResult(ctx, res, err)
		case *types.MsgAssociateContractAddress:
			res, err := msgServer.AssociateContractAddress(sdk.WrapSDKContext(ctx), msg)
			return sdk.WrapServiceResult(ctx, res, err)
		default:
			errMsg := fmt.Sprintf("unrecognized %s message type: %T", types.ModuleName, msg)
			return nil, sdkerrors.Wrap(sdkerrors.ErrUnknownRequest, errMsg)
		}
	}
}
```

**File:** x/evm/types/message_claim.go (L15-46)
```go
func NewMsgClaim(sender sdk.AccAddress, claimer common.Address) *MsgClaim {
	return &MsgClaim{Sender: sender.String(), Claimer: claimer.Hex()}
}

func (msg *MsgClaim) Route() string {
	return RouterKey
}

func (msg *MsgClaim) Type() string {
	return TypeMsgClaim
}

func (msg *MsgClaim) GetSigners() []sdk.AccAddress {
	from, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		panic(err)
	}
	return []sdk.AccAddress{from}
}

func (msg *MsgClaim) GetSignBytes() []byte {
	return sdk.MustSortJSON(ModuleCdc.MustMarshalJSON(msg))
}

func (msg *MsgClaim) ValidateBasic() error {
	_, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "Invalid sender address (%s)", err)
	}

	return nil
}
```

**File:** sei-cosmos/x/auth/ante/sigverify.go (L337-354)
```go
func (isd IncrementSequenceDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	sigTx, ok := tx.(authsigning.SigVerifiableTx)
	if !ok {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrTxDecode, "invalid transaction type")
	}

	// increment sequence of all signers
	for _, addr := range sigTx.GetSigners() {
		acc := isd.ak.GetAccount(ctx, addr)
		if err := acc.SetSequence(acc.GetSequence() + 1); err != nil {
			panic(err)
		}

		isd.ak.SetAccount(ctx, acc)
	}

	return next(ctx, tx, simulate)
}
```
