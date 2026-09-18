### Title
Front-runnable Public-Key-Only Association Precompile Allows Forced Remapping of Direct-Cast Identities, Orphaning Staking State - (File: precompiles/addr/addr.go)

### Summary
`associatePublicKey` in the `addr` precompile (`0x0000000000000000000000000000000000001004`) accepts a bare public key and, without any proof that the caller controls the corresponding private key, creates a permanent Sei⇄EVM address association and migrates any funds sitting at the "direct-cast" Sei address into the pubkey-derived address. Any unprivileged caller who has learned a victim's public key (trivially obtainable from any prior Cosmos or EVM signature by that account) can invoke this method for the victim, forcing the association at a time of the attacker's choosing.

### Finding Description
`associatePublicKey` only validates that `args[0]` parses as a secp256k1 public key; it never checks a signature proving the caller possesses the matching private key: [1](#0-0) 

Compare this with the sibling `associate` method, which legitimately requires an EIP-191 signature (`v`, `r`, `s` over a `customMessage`) to prove key ownership before deriving the same addresses: [2](#0-1) 

Both methods funnel into `associateAddresses`, which only guards against the *target* Sei address already being associated — it performs no check on whether the caller is the key owner, and no check on whether the EVM address's direct-cast identity currently holds meaningful chain state: [3](#0-2) 

The underlying `AssociateAddresses`/`MigrateBalance` helper then moves the "direct-cast" identity's bank balances and wei balance into the pubkey-derived Sei address and deletes the direct-cast base account: [4](#0-3) 

Sei-chain's own code comments document that exactly this kind of forced remap of a "mutable direct-cast EVM->Sei mapping" is dangerous because it can "orphan any staking/distribution state created under the direct-cast identity," which "can then halt the chain via the distribution validator-removal hook": [5](#0-4) 

The project mitigated this specifically for EIP‑7702 SetCode authorities by pre-associating authorities from their true pubkey before EVM execution installs delegation code (`AssociateAuthorizationAuthorities` / `AuthorityToPreAssociate`): [6](#0-5) [7](#0-6) 

However, that fix only covers the SetCode-authorization code path. The `associatePublicKey` precompile method itself remains reachable by any address, for any target public key, with no ownership proof and no check for pre-existing staking/distribution state at the direct-cast identity — i.e., the root cause (an unauthenticated identity-remap primitive) is still generally exposed to any transaction sender who knows a victim's public key.

### Impact Explanation
A caller with no special privilege can force-associate a third party's EVM/Sei identity mapping ahead of the victim's own choice, on the attacker's timing. If the victim's direct-cast Sei address already has staking delegations, unclaimed distribution rewards, or module hooks tied to it (a common state for any Sei account that has never explicitly called `associate`/`associatePublicKey` itself but has interacted with staking via its direct-cast identity), the forced remap can orphan that state exactly as flagged in the codebase's own comments, "which can then halt the chain via the distribution validator-removal hook" — i.e., a validator/chain halt. This matches the accepted impact classes (validator halt / permanent freezing of state) despite Sei having already patched the narrower SetCode variant of this exact risk.

### Likelihood Explanation
Public keys are not secret: every signed Cosmos or EVM transaction from an account discloses its public key, so any active or historical Sei user's public key is discoverable from chain history. The `associatePublicKey` call itself is a single, cheap, permissionless EVM transaction to precompile `0x1004` (50000 gas per `RequiredGas`), requiring no funds beyond gas and no cooperation from the victim. This makes the attack trivially and repeatedly executable by any EOA or contract against any address whose public key is known.

### Recommendation
Require `associatePublicKey` to prove key ownership the same way `associate` does (e.g., a signature check), or at minimum restrict it to associating `msg.sender`'s own recovered identity rather than an arbitrary supplied public key. Additionally, before performing the direct-cast → true-address migration, check for and safely handle (or reject) cases where the direct-cast identity currently has staking delegations, unbonding entries, or distribution state, mirroring the protection already added for SetCode authorities.

### Proof of Concept
1. Observe any historical transaction from a target Sei account (Cosmos or EVM) and extract its public key from the transaction's `pub_key`/signature fields.
2. From an unrelated attacker-controlled account, call the `addr` precompile at `0x0000000000000000000000000000000000001004`, method `associatePubKey(bytes compressedPubKey)`, passing the victim's compressed public key.
3. `associatePublicKey` derives `evmAddr`/`seiAddr` from the supplied key with no signature check, and — because the victim's true `seiAddr` is not yet associated — proceeds to call `associateAddresses`, which migrates all balances from the victim's direct-cast Sei address to the derived true address and deletes the direct-cast base account, as shown in `utils/helpers/associate.go` lines 34-83.
4. If the victim's direct-cast identity held staking delegations or pending distribution rewards, that state is now orphaned relative to the removed base account, matching the chain-halt risk documented in `x/evm/ante/preprocess.go` lines 103-110 for the analogous SetCode scenario.

### Citations

**File:** precompiles/addr/addr.go (L160-203)
```go
func (p PrecompileExecutor) associate(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, 0, err
	}

	// v, r and s are components of a signature over the customMessage sent.
	// We use the signature to construct the user's pubkey to obtain their addresses.
	v := args[0].(string)
	r := args[1].(string)
	s := args[2].(string)
	customMessage := args[3].(string)

	rBytes, err := decodeHexString(r)
	if err != nil {
		return nil, 0, err
	}
	sBytes, err := decodeHexString(s)
	if err != nil {
		return nil, 0, err
	}
	vBytes, err := decodeHexString(v)
	if err != nil {
		return nil, 0, err
	}

	vBig := new(big.Int).SetBytes(vBytes)
	rBig := new(big.Int).SetBytes(rBytes)
	sBig := new(big.Int).SetBytes(sBytes)

	// Derive addresses
	vBig = new(big.Int).Add(vBig, utils.Big27)

	customMessageHash := crypto.Keccak256Hash([]byte(customMessage))
	evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
}
```

**File:** precompiles/addr/addr.go (L205-236)
```go
func (p PrecompileExecutor) associatePublicKey(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}

	// Takes a single argument, a compressed pubkey in hex format, excluding the '0x'
	pubKeyHex := args[0].(string)

	pubKeyBytes, err := hex.DecodeString(pubKeyHex)
	if err != nil {
		return nil, 0, err
	}

	// Parse the compressed public key
	pubKey, err := btcec.ParsePubKey(pubKeyBytes)
	if err != nil {
		return nil, 0, err
	}

	// Convert to uncompressed public key
	uncompressedPubKey := pubKey.SerializeUncompressed()

	evmAddr, seiAddr, pubkey, err := helpers.GetAddressesFromPubkeyBytes(uncompressedPubKey)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
```

**File:** precompiles/addr/addr.go (L239-255)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}

	// Associate Addresses:
	associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.bankKeeper, p.accountKeeper)
	err = associationHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false)
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(seiAddr.String(), evmAddr)
	return ret, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** utils/helpers/associate.go (L34-83)
```go
func (p AssociationHelper) AssociateAddresses(ctx sdk.Context, seiAddr sdk.AccAddress, evmAddr common.Address, pubkey cryptotypes.PubKey, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if !castAddr.Equals(seiAddr) && p.accountKeeper.GetAccount(ctx, seiAddr) == nil {
		castAcc := p.accountKeeper.GetAccount(ctx, castAddr)
		castBaseAcc, ok := castAcc.(*authtypes.BaseAccount)
		if ok && castBaseAcc.GetPubKey() == nil && p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
			p.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(seiAddr, pubkey, castBaseAcc.GetAccountNumber(), castBaseAcc.GetSequence()))
		}
	}
	p.evmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	acc := p.accountKeeper.GetAccount(ctx, seiAddr)
	if acc == nil {
		acc = p.accountKeeper.NewAccountWithAddress(ctx, seiAddr)
	}
	if acc.GetPubKey() == nil {
		if err := acc.SetPubKey(pubkey); err != nil {
			return err
		}
		p.accountKeeper.SetAccount(ctx, acc)
	}
	return p.MigrateBalance(ctx, evmAddr, seiAddr, migrateUseiOnly)
}

func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
}
```

**File:** x/evm/ante/preprocess.go (L57-113)
```go
//nolint:revive
func (p *EVMPreprocessDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	if err := Preprocess(ctx, msg, p.evmKeeper.ChainID(ctx), p.evmKeeper.EthBlockTestConfig.Enabled); err != nil {
		return ctx, err
	}

	// use infinite gas meter for EVM transaction because EVM handles gas checking from within
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))

	derived := msg.Derived
	seiAddr := derived.SenderSeiAddr
	evmAddr := derived.SenderEVMAddr
	ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
		sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
		sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, seiAddr.String())))
	pubkey := derived.PubKey
	isAssociateTx := derived.IsAssociate
	associateHelper := helpers.NewAssociationHelper(p.evmKeeper, p.evmKeeper.BankKeeper(), p.accountKeeper)
	_, isAssociated := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if isAssociateTx && isAssociated {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "account already has association set")
	} else if isAssociateTx {
		// check if the account has enough balance (without charging)
		if !p.IsAccountBalancePositive(ctx, seiAddr, evmAddr) {
			assocErr := evmtypes.NewAssociationMissingErr(seiAddr.String())
			evmAnteMetrics.associationError.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("scenario", "associate_tx_insufficient_funds"), attribute.String("type", assocErr.AddressType())))
			return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, "account needs to have at least 1 wei to force association")
		}
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}

		return ctx.WithPriority(antedecorators.EVMAssociatePriority), nil // short-circuit without calling next
	} else if isAssociated {
		// noop; for readability
	} else {
		// not associatedTx and not already associated
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}
		if p.evmKeeper.EthReplayConfig.Enabled {
			p.evmKeeper.PrepareReplayedAddr(ctx, evmAddr)
		}
	}

	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
	p.associateAuthorizationAuthorities(ctx, msg, associateHelper)

	return next(ctx, tx, simulate)
}
```

**File:** utils/helpers/address.go (L149-188)
```go
// already associated. Mirroring validateAuthorization is essential to security: the
// authorization sig hash is computed from the auth's own ChainID, so recovery and
// auth.Authority() succeed for an authorization signed for ANY chain. Without these checks a
// publicly-visible authorization a user signed for another chain (e.g. Ethereum mainnet)
// could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their
// direct-cast balance and orphaning staking/distribution state — even though the EVM skips
// the wrong-chain authorization and installs no delegation.
func AuthorityToPreAssociate(ctx sdk.Context, k AuthorizationStateReader, auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, bool) {
	// Chain ID must be null or match the local chain.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.ChainID(ctx)) != 0 {
		return common.Address{}, nil, nil, false
	}
	// Nonce must not overflow (EIP-2681).
	if auth.Nonce+1 < auth.Nonce {
		return common.Address{}, nil, nil, false
	}
	evmAddr, seiAddr, pubkey, err := RecoverAddressesFromAuthorization(auth)
	if err != nil {
		return common.Address{}, nil, nil, false
	}
	// Cross-check against go-ethereum's authoritative recovery so we only ever act on the
	// exact address SetCode would target during execution.
	if authAddr, aerr := auth.Authority(); aerr != nil || authAddr != evmAddr {
		return common.Address{}, nil, nil, false
	}
	// Authority must have no code, or only an existing delegation designator.
	if code := k.GetCode(ctx, evmAddr); len(code) != 0 {
		if _, ok := ethtypes.ParseDelegation(code); !ok {
			return common.Address{}, nil, nil, false
		}
	}
	// Authority account nonce must match the authorization nonce.
	if k.GetNonce(ctx, evmAddr) != auth.Nonce {
		return common.Address{}, nil, nil, false
	}
	// Already-associated authorities need no pre-association (and cannot be re-mapped).
	if _, associated := k.GetEVMAddress(ctx, seiAddr); associated {
		return common.Address{}, nil, nil, false
	}
	return evmAddr, seiAddr, pubkey, true
```
