### Title
Missing chain-ID (domain) binding in EVM/Sei address-association signature allows cross-chain signature replay to force account association and balance migration - ([File: x/evm/ante/preprocess.go], [File: precompiles/addr/legacy/v601/addr.go], [File: utils/helpers/associate.go])

### Summary
The `AssociateTx` / `addr` precompile `associate()` flow recovers the signer's public key from `keccak256(CustomMessage)` with no chain ID, contract address, or nonce bound into the signed payload, unlike every other signature-verification path in the EVM module (which strictly validates `chainId`). A signature produced for one Sei deployment (e.g. testnet, a devnet, or a post-fork chain) is valid on any other Sei chain sharing the same signing key, letting anyone replay a publicly observed association signature to force-associate a victim's EVM address on a chain the victim never intended, triggering an involuntary, irreversible balance migration.

### Finding Description
Every other signature path in this codebase explicitly checks chain ID before trusting recovered addresses:
- Regular EVM transactions: `PreprocessUnpacked` / `EvmStatelessChecks` compare `ethTx.ChainId()` against the keeper's chain ID and reject mismatches. [1](#0-0) 
- EIP-7702 `SetCode` authorizations: `ChainID` is part of the RLP-encoded, hashed authorization tuple, and `AuthorityToPreAssociate` explicitly checks `auth.ChainID` against the local chain before pre-associating the authority — the code comments explicitly call out the danger of a publicly-visible authorization "signed for another chain... being replayed in a sponsored Sei tx to force-associate" a victim. [2](#0-1) 

In contrast, the `AssociateTx` custom-message path hashes only the raw message string with no chain binding: [3](#0-2) 
The same pattern exists in `HandleAssociateTx` (CheckTx path) and in the `addr` precompile's `associate()` function used by CosmWasm/EVM clients directly: [4](#0-3) [5](#0-4) 

The frontend/dApp convention even uses a fixed, non-chain-specific message text ("Please sign this message to link your EVM and Sei addresses...") with no chain ID or nonce embedded, as shown in the integration test suite that documents the canonical association message. [6](#0-5) 

Once a signature is accepted, `AssociateAddresses` immediately performs an irreversible, forced account/pubkey binding and balance migration from the EVM direct-cast address to the pubkey-derived Sei address: [7](#0-6) 

### Impact Explanation
Because the signed payload has no chain-ID or other domain separator, a signature a user creates to associate their address on one Sei network (testnet, a forked chain, or any future/alternate deployment using the same signature scheme) is a valid, replayable credential on every other Sei chain instance. An attacker who observes such a signature (they are broadcast as part of a public transaction) can submit it as a fresh `AssociateTx`/`MsgAssociate`/`addr.associate()` call on a *different* Sei chain to force-associate the victim's EVM and Sei addresses there — even though the victim never authorized association on that chain. `AssociateAddresses`/`MigrateBalance` then unconditionally moves all spendable coins and wei balance from the direct-cast address to the pubkey-derived address and can delete the direct-cast account, matching the exact "migrating their direct-cast balance and orphaning staking/distribution state" hazard this codebase's own EIP-7702 fix was written to prevent. This is a real fund-custody/account-integrity impact (forced, non-consensual balance migration and account restructuring) reachable by any unprivileged transaction sender who has ever obtained one valid association signature from the victim.

### Likelihood Explanation
The message the wallet is prompted to sign is a fixed public string documented in the test suite (no chain ID, no nonce, no domain separator), so any signature produced for association on one chain will validate on any other chain using this exact code path. This requires only that the attacker capture one signature (trivial, since these are broadcast on-chain) and submit it as a new transaction/precompile call to a different Sei chain instance — no privileged access, validator collusion, or complex setup required.

### Recommendation
Bind the signed digest to the specific chain (and ideally to a purpose/domain string and the account's own address) before hashing, mirroring the EIP-7702 fix already present in this codebase:
- Include `ChainID` (and preferably a nonce and the target contract/module identity) in the byte payload that is hashed in `PreprocessUnpacked` (`x/evm/ante/preprocess.go`), `HandleAssociateTx` (`app/ante/evm_checktx.go`), and the `associate()` precompile implementations (`precompiles/addr/legacy/*/addr.go`), the same way `RecoverAddressesFromAuthorization` incorporates `auth.ChainID` via RLP encoding.
- Reject association messages that do not embed the current chain's chain ID, consistent with `AuthorityToPreAssociate`'s explicit chain-ID check.

### Proof of Concept
1. On Sei Chain A (e.g. testnet), a user's wallet signs the fixed association message `"Please sign this message to link your EVM and Sei addresses..."` producing `(v, r, s)`, and submits it via `addr.associate(v, r, s, message)` or an `AssociateTx`. [5](#0-4) 
2. An attacker observes this signature on-chain (it is public in the transaction).
3. The attacker submits the identical `(v, r, s, message)` tuple to the `associate()` precompile / `AssociateTx` / `MsgAssociate` on Sei Chain B (a different deployment sharing the same signing scheme, e.g. a forked chain or a future network where the user has not yet consented to associate).
4. `PreprocessUnpacked`/`HandleAssociateTx` recovers the same EVM address, Sei address, and pubkey from the signature (since chain ID is never part of the hash) and calls `AssociateAddresses`, which force-links the accounts and immediately migrates the direct-cast address's spendable coins and wei balance to the pubkey-derived address. [7](#0-6) 
5. The victim's balance and account state on Chain B are forcibly rearranged without their consent for that specific chain, matching the reported "signature reuse across chains due to missing chain-ID binding" bug class.

### Citations

**File:** app/ante/evm_checktx.go (L138-153)
```go
	// validate chain ID on the transaction
	txChainID := etx.ChainId()
	switch etx.Type() {
	case ethtypes.LegacyTxType:
		// legacy either can have a zero or correct chain ID
		if txChainID.Cmp(big.NewInt(0)) != 0 && txChainID.Cmp(chainID) != 0 {
			logger.Debug("chainID mismatch", "txChainID", txChainID, "chainID", chainID)
			return sdkerrors.ErrInvalidChainID
		}
	default:
		// after legacy, all transactions must have the correct chain ID
		if txChainID.Cmp(chainID) != 0 {
			logger.Debug("chainID mismatch", "txChainID", txChainID, "chainID", chainID)
			return sdkerrors.ErrInvalidChainID
		}
	}
```

**File:** app/ante/evm_checktx.go (L182-204)
```go
func HandleAssociateTx(ctx sdk.Context, ek *evmkeeper.Keeper, atx *ethtx.AssociateTx, readOnly bool) (sdk.Context, error) {
	V, R, S := atx.GetRawSignatureValues()
	V = new(big.Int).Add(V, utils.Big27)
	// Hash custom message passed in
	customMessageHash := crypto.Keccak256Hash([]byte(atx.CustomMessage))
	evmAddr, seiAddr, seiPubkey, err := helpers.GetAddresses(V, R, S, customMessageHash)
	if err != nil {
		return ctx, err
	}
	_, isAssociated := ek.GetEVMAddress(ctx, seiAddr)
	if isAssociated {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "account already has association set")
	}
	if !IsAccountBalancePositive(ctx, ek, seiAddr, evmAddr) {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, "account needs to have at least 1 wei to force association")
	}
	if !readOnly {
		if err := AssociateAddress(ctx, ek, evmAddr, seiAddr, seiPubkey); err != nil {
			return ctx, err
		}
	}
	return ctx.WithPriority(antedecorators.EVMAssociatePriority), nil
}
```

**File:** utils/helpers/address.go (L116-160)
```go
// RecoverAddressesFromAuthorization recovers the EVM address, Sei address, and public
// key of the account that signed an EIP-7702 SetCode authorization (the "authority").
// The authorization sig hash is keccak256(0x05 || rlp([chainId, address, nonce])) and
// the recovery id is carried directly in auth.V (yParity, 0 or 1), which GetAddresses
// expects bumped by 27. This mirrors go-ethereum's SetCodeAuthorization.Authority(), but
// additionally returns the recovered public key so the authority can be associated with
// its true Sei address.
func RecoverAddressesFromAuthorization(auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	var buf bytes.Buffer
	buf.WriteByte(eip7702MagicPrefix)
	if err := rlp.Encode(&buf, []any{auth.ChainID, auth.Address, auth.Nonce}); err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	sigHash := crypto.Keccak256Hash(buf.Bytes())
	v := new(big.Int).SetUint64(uint64(auth.V) + 27)
	return GetAddresses(v, auth.R.ToBig(), auth.S.ToBig(), sigHash)
}

// AuthorizationStateReader exposes the EVM state lookups needed to decide whether an
// EIP-7702 authorization would be applied. Both the standard and giga EVM keepers satisfy it.
type AuthorizationStateReader interface {
	ChainID(ctx sdk.Context) *big.Int
	GetCode(ctx sdk.Context, addr common.Address) []byte
	GetNonce(ctx sdk.Context, addr common.Address) uint64
	GetEVMAddress(ctx sdk.Context, seiAddr sdk.AccAddress) (common.Address, bool)
}

// AuthorityToPreAssociate returns the authority of an EIP-7702 authorization that should be
// associated with its true (pubkey-derived) Sei address before execution, or ok=false.
//
// It mirrors go-ethereum's StateTransition.validateAuthorization (chain id, nonce overflow,
// authority code, and account-nonce checks) so that pre-association happens only for
// authorizations the EVM will actually apply, and additionally skips authorities that are
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
```

**File:** x/evm/ante/preprocess.go (L189-206)
```go
	if atx, ok := txData.(*ethtx.AssociateTx); ok {
		V, R, S := atx.GetRawSignatureValues()
		V = new(big.Int).Add(V, utils.Big27)
		// Hash custom message passed in
		customMessageHash := crypto.Keccak256Hash([]byte(atx.CustomMessage))
		evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(V, R, S, customMessageHash)
		if err != nil {
			return err
		}
		msgEVMTransaction.Derived = &derived.Derived{
			SenderEVMAddr: evmAddr,
			SenderSeiAddr: seiAddr,
			PubKey:        &secp256k1.PubKey{Key: pubkey.Bytes()},
			Version:       derived.Cancun,
			IsAssociate:   true,
		}
		return nil
	}
```

**File:** precompiles/addr/legacy/v601/addr.go (L154-197)
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

**File:** integration_test/precompile_tests/precompiles/addr.spec.ts (L26-38)
```typescript
/** The canonical association message users sign (mirrors the legacy hardhat suite). */
const ASSOCIATE_MESSAGE =
    'Please sign this message to link your EVM and Sei addresses. No SEI will be spent as a result of this signature.\n\n';

/** EIP-191 envelope the precompile hashes: "\x19Ethereum Signed Message:\n<len><message>". */
const eip191Envelope = (message: string): string =>
    `\x19Ethereum Signed Message:\n${Buffer.from(message, 'utf8').length}${message}`;

/** v/r/s in the precompile's expected shape: v is the 0/1 recovery id as hex. */
const signatureParts = async (wallet: EvmAccount, message: string) => {
    const sig = ethers.Signature.from(await wallet.wallet.signMessage(message));
    return { v: `0x${sig.v - 27}`, r: sig.r, s: sig.s };
};
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
