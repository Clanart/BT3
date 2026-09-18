### Title
Missing Message-Content Binding in `addr` Precompile `associate()` Enables Cross-Context Signature Replay to Force Unwanted Address Association and Balance Migration - (File: `precompiles/addr/addr.go`)

### Summary
The `associate` method of the `addr` precompile (address `0x0000000000000000000000000000000000001004`) recovers an EVM/Sei address pair from an arbitrary caller-supplied `(v, r, s, customMessage)` tuple and immediately performs an on-chain association plus balance migration, without ever checking that `customMessage` is the canonical association prompt the front end is supposed to have shown the user. Any `personal_sign`-style signature the victim produced for a completely unrelated purpose (login/SIWE message, off-chain order, another dApp's message) can be replayed by anyone as the `associate` call's arguments, forcing the victim's implicit cast-account funds to be migrated and their EVM↔Sei association permanently fixed without their consent for this specific action. This mirrors the Mastodon bug class: a signature legitimately produced for one purpose is accepted by the verifier as authorization for a different, unintended claim because the verifier does not bind the signature to the specific semantic content it is meant to authorize.

### Finding Description
`associate()` takes `v`, `r`, `s`, and a free-form `customMessage` string directly from the caller: [1](#0-0) 

It hashes `customMessage` with plain `crypto.Keccak256Hash` and feeds that hash straight into ECDSA recovery via `helpers.GetAddresses`: [2](#0-1) 

Nowhere in this path does the precompile verify that `customMessage` matches (or even starts with) the expected association prompt ("Please sign this message to link your EVM and Sei addresses…"). The EIP-191 prefix (`"\x19Ethereum Signed Message:\n<len><message>"`) is expected to already be embedded inside `customMessage` by the *client*, not enforced by the contract — this is visible in both the JS/TS test helpers that manually build the envelope before signing: [3](#0-2) 

Because the contract performs no content check, any historically-observed `(customMessage, v, r, s)` triple that recovers to a valid secp256k1 public key is accepted — regardless of what the message actually says or who intended it for what purpose. If `customMessage` is set to the exact bytes hashed in some other, unrelated `personal_sign` flow (e.g. a "Sign-In-With-Ethereum" prompt, an exchange/dApp login challenge, or any other plaintext message a wallet signed elsewhere), replaying that publicly-observable signature here recovers the victim's real public key and forces:

1. Permanent EVM↔Sei address association (`SetAddressMapping`), which cannot later be changed because the "already associated" guard becomes active: [4](#0-3) 
2. Balance migration from the victim's implicit cast-account (`sdk.AccAddress(evmAddr[:])`) to the newly bound pubkey-derived Sei account, executed unconditionally as part of `AssociateAddresses`: [5](#0-4) 

The root cause is the absence of any domain-separation / canonical-content check binding the signed hash to the specific "I intend to associate my address" claim — exactly the same class of defect as the Mastodon report, where a value trusted as attribution/authorization was not actually covered by an effective signature check tying it to intended semantics.

### Impact Explanation
This lets an unprivileged third party trigger, without the victim's consent for this specific action, an irreversible on-chain state change (permanent address binding) and a state-mutating fund movement (`MigrateBalance`) for the victim's account. Because association is permanent and gated by an "already associated" check, an attacker can front-run the timing of this migration — forcing it to occur at an attacker-chosen moment, potentially interfering with the victim's expectations about which Sei account currently custodies funds derived from their cast address, and permanently fixing which pubkey secures their Sei-side account before the victim is ready. This qualifies as an unauthorized, precompile-triggered transfer of funds (moving balance out of the cast account) executed by a party other than the account owner submitting the association themselves, satisfying the "unauthorized transfer via precompile" impact bar even though the ultimate Sei-side beneficiary key is cryptographically tied to the same private key.

### Likelihood Explanation
Exploitation requires only observing a previously-produced `personal_sign`-style signature and plaintext message from the victim (many dApps' SIWE/login challenges are plaintext and often logged or observable), then submitting a single unprivileged EVM transaction calling `associate` with those bytes. No special privileges, gas advantage, or validator collusion is needed — the call is a normal public transaction to a public-facing precompile at a well-known address, making this readily reachable by any transaction sender.

### Recommendation
Enforce a canonical, contract-checked message format for `associate` (e.g. require `customMessage` to exactly equal a fixed prefix plus the caller's own claimed Sei address / a fresh on-chain nonce / current EVM address, verified inside the precompile) so a signature can only be replayed for the specific association it was created for. Alternatively, embed a domain separator (chain ID + purpose tag + nonce) that the contract itself constructs and requires to appear verbatim in the signed content, rather than trusting free-form `customMessage` supplied at call time.

### Proof of Concept
1. Victim signs an unrelated `personal_sign` message elsewhere, e.g. `"Sign in to ExampleApp: session=1234"`, producing `(v, r, s)` over `keccak256("\x19Ethereum Signed Message:\n37Sign in to ExampleApp: session=1234")`. This message/signature pair is publicly observable (e.g., via network logs, replayed by a malicious site, or leaked by ExampleApp).
2. Attacker (any address, unprivileged) submits an EVM transaction calling `associate(v, r, s, "\x19Ethereum Signed Message:\n37Sign in to ExampleApp: session=1234")` on `0x...1004`, per `precompiles/addr/addr.go:160-203`.
3. `helpers.GetAddresses` recovers the victim's real pubkey/EVM address/Sei address from the replayed signature; `associateAddresses` finds no existing mapping and proceeds to call `AssociateAddresses`, which sets the permanent mapping and migrates any `usei`/wei balance sitting at the victim's implicit cast Sei address to the newly bound account — all without the victim ever calling the precompile or intending this specific association at this time.

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

**File:** precompiles/addr/addr.go (L239-254)
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
```

**File:** utils/helpers/address.go (L44-81)
```go
func GetAddresses(V *big.Int, R *big.Int, S *big.Int, data common.Hash) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	pubkey, err := RecoverPubkey(data, R, S, V, true)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}

	return GetAddressesFromPubkeyBytes(pubkey)
}

func GetAddressesFromPubkeyBytes(pubkey []byte) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	evmAddr, err := PubkeyToEVMAddress(pubkey)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	seiPubkey := PubkeyBytesToSeiPubKey(pubkey)
	seiAddr := sdk.AccAddress(seiPubkey.Address())
	return evmAddr, seiAddr, &seiPubkey, nil
}

// first half of go-ethereum/core/types/transaction_signing.go:recoverPlain
func RecoverPubkey(sighash common.Hash, R, S, Vb *big.Int, homestead bool) ([]byte, error) {
	if Vb.BitLen() > 8 || Vb.Uint64() < 27 {
		return []byte{}, ethtypes.ErrInvalidSig
	}
	V := byte(Vb.Uint64() - 27) //nolint:gosec // the bit-length and lower-bound checks make the subtraction fit in one byte.
	if !crypto.ValidateSignatureValues(V, R, S, homestead) {
		return []byte{}, ethtypes.ErrInvalidSig
	}
	// encode the signature in uncompressed format
	r, s := R.Bytes(), S.Bytes()
	sig := make([]byte, crypto.SignatureLength)
	copy(sig[32-len(r):32], r)
	copy(sig[64-len(s):64], s)
	sig[64] = V

	// recover the public key from the signature
	return crypto.Ecrecover(sighash[:], sig)
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
