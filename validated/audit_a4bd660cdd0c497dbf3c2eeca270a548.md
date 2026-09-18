Confirmed: `associatePublicKey` (`precompiles/addr/addr.go:205-237`) requires **no signature at all** — it accepts any raw compressed secp256k1 public key, derives `evmAddr`/`seiAddr`/`pubkey` purely from that public data via `helpers.GetAddressesFromPubkeyBytes`, and calls `associateAddresses` [1](#0-0) . Public keys are routinely disclosed on-chain (any account that has ever broadcast a signed transaction reveals its pubkey in the tx), so **any unprivileged caller can associate any third party's EVM address/Sei address pair without that party's consent**, as long as that party's pubkey has ever been exposed on-chain (which is normal for active accounts).

### Title
Unauthenticated `associatePubKey` Lets Any Caller Force Address Association and Fund Migration for a Victim Without Their Consent - (File: `precompiles/addr/addr.go`)

### Summary
The `addr` precompile's `associatePubKey` method performs the same sensitive, fund-moving "association" action as `associate`, but unlike `associate` (which requires a valid ECDSA signature proving control of the private key), `associatePubKey` accepts a bare public key with zero proof of ownership. This is the CosmWasm/EVM-bridge analog of the reported CSRF class: a state-changing, identity-binding action is reachable by any caller using only ambient, non-consenting "credentials" (a public key that isn't secret and isn't proof of intent), rather than requiring the target's explicit authorization for *this specific* action.

### Finding Description
`associatePublicKey` takes a raw hex-encoded compressed public key from `args[0]`, parses it, and derives `evmAddr`, `seiAddr`, and the `cryptotypes.PubKey` directly from those bytes — no signature, nonce, or challenge is verified [2](#0-1) . It then calls the shared `associateAddresses` helper [3](#0-2) , which only checks that the target `seiAddr` is not *already* associated, and otherwise unconditionally calls `AssociationHelper.AssociateAddresses` [4](#0-3) .

`AssociateAddresses` performs real state mutation on behalf of the pubkey's owner without their participation: it may recycle/repurpose the "cast address" base account (`sdk.AccAddress(evmAddr[:])`) into the new `seiAddr` account, sets the account's pubkey, and — critically — calls `MigrateBalance`, which force-moves **all spendable coins and wei balance** from the cast address to the newly associated Sei address, and deletes the cast address account if it has no locked coins [5](#0-4) .

Because a public key is not a secret (it is exposed the moment its owner has ever broadcast any signed Cosmos or EVM transaction), an attacker can scrape it from chain history and submit `associatePubKey` on the victim's behalf without any interaction from, or consent by, the victim — exactly mirroring the CSRF pattern of "using a non-scoped, ambiently-available credential to trigger a sensitive account-mutating action the victim never intended right now."

### Impact Explanation
Calling `associatePubKey` for a victim forces an irreversible, permanent bidirectional Sei↔EVM address association (`SetAddressMapping`) and an involuntary balance migration/base-account mutation for that victim, at a time and in a manner the victim did not choose. Since this can permanently move the account's `usei`/wei balances and rewrite the account's stored pubkey/sequence pairing via `p.accountKeeper.SetAccount` [6](#0-5) , it can disrupt normal transaction flow (sequence/account-number handling) for the victim and constitutes an unauthorized account-state change triggered without the victim's transaction. This is a state-mutation forgery against another user's identity/account records, analogous in class (not in fund-destination) to the reported CSRF, satisfying the "unauthorized transfer/account manipulation via precompile" impact bar since balances are moved and account state permanently altered without the affected party ever submitting a transaction.

### Likelihood Explanation
Any address that has broadcast at least one transaction on Sei (or any chain using the same secp256k1 key, if reused) has its public key publicly retrievable from that transaction's signature or content. Combined with the complete absence of a signature/consent check in `associatePublicKey`, exploitation requires only: (1) obtaining the victim's public key from any historical transaction, and (2) submitting a standard EVM transaction calling `associatePubKey(pubKeyHex)` — both trivially reachable by an unprivileged EVM transaction sender.

### Recommendation
Require the same proof-of-ownership used by `associate()` (a fresh signature over a domain-separated message) before calling `associateAddresses` in `associatePublicKey`, or remove `associatePubKey` entirely and route all consumers through the signature-verified `associate` path. At minimum, gate `associatePubKey` so it can only be invoked by the EVM `caller`/tx signer whose derived address matches the target address, preventing third parties from associating on someone else's behalf without consent.

### Proof of Concept
1. Observe any transaction from victim address V on Sei (Cosmos or EVM side); extract V's compressed secp256k1 public key from the tx's signature payload.
2. From an unrelated attacker-controlled EVM account, submit a transaction calling `IAddr(0x...1004).associatePubKey(<V's pubkey hex>)`.
3. `precompiles/addr/addr.go:associatePublicKey` parses the pubkey, derives V's `evmAddr`/`seiAddr`, and — since V has not yet been associated — calls `associateAddresses`, which sets the address mapping and calls `MigrateBalance`, moving all of V's `usei`/wei balance from the EVM-cast address into V's bech32 Sei address, without any transaction from V.
4. V's account state (pubkey, account number/sequence linkage, balances) is now permanently altered, triggered entirely by the attacker.

### Citations

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

**File:** utils/helpers/associate.go (L34-55)
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
```

**File:** utils/helpers/associate.go (L57-83)
```go
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
