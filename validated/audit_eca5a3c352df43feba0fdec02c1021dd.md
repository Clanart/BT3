### Title
Griefer can front-run and permanently block a legacy account's `solo` claim via signature/sequence replay - (`precompiles/solo/solo.go`)

### Summary
The `solo` precompile's `claim`/`claimSpecific` flow accepts a fully-signed, self-contained Cosmos `SigVerifiableTx` (wrapping `MsgClaim`/`MsgClaimSpecific`) as raw EVM calldata and validates it against the signer account's current on-chain sequence number. Because this signed tx blob is visible in the public mempool as plain calldata, and `MsgClaim`/`MsgClaimSpecific` are registered as ordinary `sdk.Msg` types, an attacker can extract the blob and re-broadcast it directly as a bare Cosmos transaction, consuming the account's sequence before the original `claim` EVM call executes — causing the legitimate claim to permanently revert with a sequence mismatch. This is the same signature/nonce front-running griefing pattern described in the external report for `Erc20Votes::delegateBySig`.

### Finding Description
`precompiles/solo/solo.go`'s `validate` function decodes `args[0]` into a Cosmos `Tx` and extracts a `MsgClaim`/`MsgClaimSpecific`: [1](#0-0) 

Signature verification happens in `sigverify`, which checks that the embedded signature's sequence number matches the account's *current* on-chain sequence, and only then increments it: [2](#0-1) 

`MsgClaim` and `MsgClaimSpecific` are registered as normal `sdk.Msg` implementations in the interface registry and legacy amino codec, meaning they are structurally valid, independently signable/broadcastable Cosmos transactions, not something whose validity is scoped only to being decoded inside the precompile: [3](#0-2) 

`MsgClaim` itself is a plain `sdk.Msg` with its own `GetSigners`/`GetSignBytes`/`ValidateBasic`: [4](#0-3) 

Attack flow, mirroring the reported `delegateBySig` grief:
1. A legacy Cosmos account holder builds and signs a `MsgClaim`/`MsgClaimSpecific` tx (embedding their current account sequence) and submits it as calldata to the `solo` precompile's `claim` method in an EVM transaction.
2. This EVM transaction (with the raw signed Cosmos tx bytes as calldata) sits in the public mempool.
3. A griefer observes the pending transaction, extracts the embedded raw signed Cosmos tx bytes from the calldata, and re-broadcasts those bytes directly as a standalone Cosmos transaction (bypassing the EVM/precompile entirely).
4. The ante handler's signature/sequence verification accepts this valid, correctly-sequenced signature and increments the account's sequence as part of normal tx processing (this happens regardless of what msg-level routing/execution occurs afterward).
5. When the original `claim` EVM transaction is later processed, `sigverify` finds `sig.Sequence != acct.GetSequence()` and returns `"account sequence mismatch for claim tx"`, reverting the entire claim.

The test suite explicitly documents that a sequence mismatch causes the claim to fail, confirming the exploitable check: [5](#0-4) 

### Impact Explanation
This blocks a legacy Cosmos-native account holder from completing their fund migration to an EVM address via the Solo `claim`/`claimSpecific` mechanism, denying access to funds that are meant to move via this precompile (native coins, CW20, CW721 balances per `ClaimSpecific`): [6](#0-5) 
Because the griefer only needs to observe the mempool and rebroadcast public calldata — no private key or special privilege is required — this is a persistent, repeatable denial-of-service: every time the victim re-signs with a fresh sequence and resubmits, the griefer can again intercept and front-run it, indefinitely blocking that account from ever completing the claim. This is a reachable, unprivileged griefing/DoS vector against a core fund-migration precompile, not merely a resource/informational issue.

### Likelihood Explanation
Likelihood is high whenever the sender's EVM transaction (containing the claim calldata) is broadcast to a public mempool before inclusion — which is the normal, expected path for any EVM transaction. Extracting the raw signed tx bytes from calldata requires no special access, and rebroadcasting a validly signed Cosmos tx is a completely standard, always-available capability of any network participant.

### Recommendation
Do not let a globally valid, replayable signed Cosmos tx object be the sole gating mechanism for a security-critical claim, since its sequence-bound validity can be raced/burned by any third party who can see it. Options:
- Bind the claim signature to a purpose/domain-specific nonce that only the `claim` precompile call can consume (not the general account sequence, which any tx targeting that account touches), so that observing/replaying the blob elsewhere cannot invalidate it.
- Have `sigverify` tolerate a legitimate sequence bump by re-validating using the exact signed `signerData.Sequence` embedded at signing time (rather than requiring exact equality with the live account sequence) combined with a dedicated used-signature/claim-nonce set, so a spurious sequence bump by a third party does not permanently invalidate the pending claim.
- Alternatively, require the `claim` signature payload to be bound to something unforgeable-by-replay outside the precompile context (e.g., include the EVM caller/claimer and a precompile-specific nonce inside the signed payload, decoupled from the account's generic tx sequence), and reject/no-op if the underlying raw tx is ever submitted as a standalone Cosmos tx (e.g., by ensuring `MsgClaim`/`MsgClaimSpecific` have no routable handler outside the precompile context or explicitly reject them at ante time when not wrapped by the precompile invocation).

### Proof of Concept
1. Victim (holder of legacy account `claimee`) builds `MsgClaim{Sender: claimee, Claimer: evmAddr}`, signs it with sequence `N`, and calls the `solo` precompile's `claim(bytes)` method with the signed tx bytes as `args[0]`, broadcasting this as a normal EVM transaction.
2. Attacker monitors the mempool, decodes the EVM tx's calldata, and extracts the embedded signed Cosmos tx bytes (which are just standard `sdk.Tx` bytes wrapping a registered `sdk.Msg`).
3. Attacker rebroadcasts these exact bytes as a plain Cosmos transaction (not via EVM). The ante handler validates the signature against sequence `N` (still valid) and, upon acceptance, sets the account's sequence to `N+1` per the same logic used in `sigverify`: [7](#0-6) 
4. The victim's original `claim` EVM transaction is processed afterward; `sigverify` now sees `sig.Sequence (N) != acct.GetSequence() (N+1)` and returns the error path exercised by the repo's own test: [5](#0-4) 
5. The victim's `claim` call reverts and their funds remain stuck in the legacy account pending a fresh signature — which the attacker can again race and burn, repeating the denial-of-service indefinitely.

### Citations

**File:** precompiles/solo/solo.go (L140-228)
```go
func (p PrecompileExecutor) Claim(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, readOnly bool) (ret []byte, remainingGas uint64, err error) {
	claimMsg, sender, err := p.validate(ctx, caller, args, readOnly)
	if err != nil {
		return nil, 0, err
	}
	_, ok := claimMsg.(claimSpecificMsg)
	if ok {
		return nil, 0, errors.New("message for Claim must not be MsgClaimSpecific type")
	}
	if err := p.bankKeeper.SendCoins(ctx, sender,
		p.evmKeeper.GetSeiAddressOrDefault(ctx, caller), p.bankKeeper.GetAllBalances(ctx, sender)); err != nil {
		return nil, 0, fmt.Errorf("failed to transfer coins: %w", err)
	}
	bz, err := method.Outputs.Pack(true)
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}

func (p PrecompileExecutor) ClaimSpecific(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, readOnly bool) (ret []byte, remainingGas uint64, err error) {
	claimMsg, sender, err := p.validate(ctx, caller, args, readOnly)
	if err != nil {
		return nil, 0, err
	}
	claimSpecificMsg, ok := claimMsg.(claimSpecificMsg)
	if !ok {
		return nil, 0, errors.New("message is not MsgClaimSpecific type")
	}
	callerSeiAddr := p.evmKeeper.GetSeiAddressOrDefault(ctx, caller)
	for _, asset := range claimSpecificMsg.GetIAssets() {
		if asset.IsNative() {
			denom := asset.GetDenom()
			balance := p.bankKeeper.GetBalance(ctx, sender, denom)
			if !balance.IsZero() {
				if err := p.bankKeeper.SendCoins(ctx, sender, callerSeiAddr, sdk.NewCoins(balance)); err != nil {
					return nil, 0, err
				}
			}
			continue
		}
		contractAddr, err := sdk.AccAddressFromBech32(asset.GetContractAddress())
		if err != nil {
			return nil, 0, fmt.Errorf("failed to parse contract address %s: %w", asset.GetContractAddress(), err)
		}
		switch {
		case asset.IsCW20():
			res, err := p.wasmViewKeeper.QuerySmartSafe(ctx, contractAddr, CW20BalanceQueryPayload(sender))
			if err != nil {
				return nil, 0, fmt.Errorf("failed to query CW20 contract %s for balance: %w", contractAddr.String(), err)
			}
			balance, err := ParseCW20BalanceQueryResponse(res)
			if err != nil {
				return nil, 0, fmt.Errorf("failed to parse CW20 contract %s balance response: %w", contractAddr.String(), err)
			}
			_, err = p.wasmKeeper.Execute(ctx, contractAddr, sender, CW20TransferPayload(callerSeiAddr, balance), sdk.NewCoins())
			if err != nil {
				return nil, 0, fmt.Errorf("failed to transfer on CW20 contract %s: %w", contractAddr.String(), err)
			}
		case asset.IsCW721():
			allTokens := []string{}
			if token := asset.GetDenom(); token != "" {
				allTokens = append(allTokens, token)
			} else {
				startAfter := ""
				for {
					res, err := p.wasmViewKeeper.QuerySmartSafe(ctx, contractAddr, CW721TokensQueryPayload(sender, startAfter))
					if err != nil {
						return nil, 0, fmt.Errorf("failed to query CW721 contract %s for all tokens: %w", contractAddr.String(), err)
					}
					tokens, err := ParseCW721TokensQueryResponse(res)
					if err != nil {
						return nil, 0, fmt.Errorf("failed to parse CW20 contract %s balance response: %w", contractAddr.String(), err)
					}
					if len(tokens) == 0 {
						break
					}
					allTokens = append(allTokens, tokens...)
					startAfter = tokens[len(tokens)-1]
				}
			}
			for _, token := range allTokens {
				_, err := p.wasmKeeper.Execute(ctx, contractAddr, sender, CW721TransferPayload(callerSeiAddr, token), sdk.NewCoins())
				if err != nil {
					return nil, 0, fmt.Errorf("failed to transfer token %s on CW721 contract %s: %w", token, contractAddr.String(), err)
				}
			}
		}
	}
	bz, err := method.Outputs.Pack(true)
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

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

**File:** precompiles/solo/solo.go (L284-313)
```go
	pubkeyAddr := sdk.AccAddress(pubkey.Address())
	if !bytes.Equal(pubkeyAddr, sender) {
		return fmt.Errorf("claim message is for %s but was signed by %s", sender.String(), pubkeyAddr.String())
	}
	sigs, err := sigTx.GetSignaturesV2()
	if err != nil {
		return fmt.Errorf("failed to get signatures due to %w", err)
	}
	if len(sigs) != 1 {
		return fmt.Errorf("claim tx should have exactly 1 signature but got %d", len(sigs))
	}
	sig := sigs[0]
	if sig.Sequence != acct.GetSequence() {
		return fmt.Errorf("account sequence mismatch for claim tx (%d vs. %d)", sig.Sequence, acct.GetSequence())
	}
	if err := authante.DefaultSigVerificationGasConsumer(ctx.GasMeter(), sig, p.accountKeeper.GetParams(ctx)); err != nil {
		return fmt.Errorf("insufficient gas for sig verification: %w", err)
	}
	signerData := authsigning.SignerData{
		ChainID:       ctx.ChainID(),
		AccountNumber: acct.GetAccountNumber(),
		Sequence:      acct.GetSequence(),
	}
	if err := authsigning.VerifySignature(pubkey, signerData, sig.Data, p.txConfig.SignModeHandler(), tx); err != nil {
		return fmt.Errorf("failed to verify signature for claim tx: %w", err)
	}
	// increment sequence
	_ = acct.SetSequence(acct.GetSequence() + 1)
	p.accountKeeper.SetAccount(ctx, acct)
	return nil
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

**File:** x/evm/types/message_claim.go (L9-46)
```go
const TypeMsgClaim = "evm_claim"

var (
	_ sdk.Msg = &MsgClaim{}
)

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

**File:** precompiles/solo/solo_test.go (L114-120)
```go
	// sequence number mismatch
	ctx, _ = origCtx.CacheContext()
	ctx = ctx.WithGasMeter(sdk.NewGasMeter(1000000, 1, 1))
	acc.Sequence++
	_, remainingGas, err = p.Claim(ctx, claimer, &method, []interface{}{signClaimMsg(t, evmtypes.NewMsgClaim(claimee, claimer), claimee, claimer, acc, claimeeKey)}, false)
	require.Error(t, err, "account sequence mismatch")
	require.Equal(t, uint64(0), remainingGas)
```
