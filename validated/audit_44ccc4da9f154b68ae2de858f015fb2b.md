Confirmed: `AGG_SIG_UNSAFE`'s message check in `pkm_pairs_for_conditions_dict` / `pkm_pairs` verifies the signature over exactly `msg` with no coin-id or spend-context appended, only guarding against the message *ending with* one of the `AGG_SIG_*` additional-data suffixes [1](#0-0) [2](#0-1) . This is the same raw-message BLS signing format (`AugSchemeMPL.sign(secret_key, message_bytes)`) produced when a message-signing caller sets `safe_mode=False` on `SignMessageByAddress`/`SignMessageByID` [3](#0-2) [4](#0-3) .

### Title
Caller-Controlled `safe_mode` on Wallet Message-Signing RPC Allows Bypassing CHIP-0002 Domain Separation, Enabling Signature Reuse as an `AGG_SIG_UNSAFE` Authorization - (File: chia/wallet/util/signing.py, chia/wallet/wallet_rpc_api.py)

### Summary
`sign_message_by_address` / `sign_message_by_id` accept a `safe_mode` flag from the caller (RPC client / dapp / WalletConnect-style integration) that determines whether the CHIP-0002 domain-separation prefix (`"Chia Signed Message"`) is applied before signing [5](#0-4) . When `safe_mode=False`, the wallet signs the caller-supplied bytes directly with `AugSchemeMPL.sign(secret_key, message)` — the exact same private key (`convert_secret_key_to_synthetic`) and exact same raw-BLS-augmentation format used to authorize an `AGG_SIG_UNSAFE` condition on-chain [6](#0-5) [3](#0-2) . This mirrors the reported bug class: the requester of a signature controls a toggle (`displayMessage` / here `safe_mode`) that removes the safety wrapper meant to guarantee the signature can't be misused/misread by the user, without the wallet enforcing the safeguard.

### Finding Description
CHIP-0002 message signing intentionally wraps the message in `("Chia Signed Message", message)` and hashes it before signing, specifically to domain-separate "prove I own this address" signatures from raw signatures that could satisfy on-chain spend conditions [7](#0-6) . The RPC/CLI request objects `SignMessageByAddress`/`SignMessageByID` expose a `safe_mode: bool = True` field fully controlled by the caller; setting it `False` switches the wallet to `BLS_MESSAGE_AUGMENTATION_UTF8_INPUT`/`HEX_INPUT` mode, which signs the raw bytes with no domain separation whatsoever [8](#0-7) [3](#0-2) .

That raw-signature format is byte-for-byte identical to what the consensus layer accepts for `AGG_SIG_UNSAFE`: `pkm_pairs_for_conditions_dict`/`pkm_pairs` require only that the public key matches and the signed message doesn't end with a reserved `AGG_SIG_*` suffix — there is no coin id, no nonce, no purpose tag baked into the signature [2](#0-1) [1](#0-0) . Any signature produced this way for one purpose (e.g., a "login" or "prove ownership" message a dapp asked the user to sign) can be replayed by that dapp (or anyone it shares the signature with) as the `AGG_SIG_UNSAFE` signature satisfying a coin's spend conditions requiring that exact public key/message pair, as long as the attacker also controls a puzzle that emits a matching `AGG_SIG_UNSAFE` condition — this is exactly the risk CHIP-0002's mandatory prefix was designed to eliminate, and the RPC lets the requester turn that protection off.

The `is_hex`/`safe_mode` combination is entirely dictated by the request, i.e. the untrusted party asking the user's wallet to sign a message (analogous to the dapp in the Solflare/Aptos/Sui report) decides whether domain separation is applied — the wallet backend does not enforce it.

### Impact Explanation
A malicious dapp/integration can request the wallet sign a message with `safe_mode=False`, `is_hex=True`, presenting the request to the user as an innocuous login/verification prompt. Since the resulting BLS signature is over the raw bytes with the wallet's real signing key, and `AGG_SIG_UNSAFE` on-chain validation accepts any message not ending in a reserved suffix, that exact (pubkey, message, signature) triple can potentially be reused to satisfy an `AGG_SIG_UNSAFE` condition crafted by the attacker in a coin spend, enabling unauthorized use of the user's key material to co-sign a transaction the user never intended — this is a signature-reuse / unauthorized-coin-movement risk (medium/high depending on how attacker constructs the puzzle requiring that condition).

### Likelihood Explanation
Exploitation requires: (1) the caller can choose `safe_mode=False` unilaterally (confirmed — client controlled, default only applies if unset) [9](#0-8) , and (2) crafting a puzzle/coin that expects an `AGG_SIG_UNSAFE` signature matching an attacker-chosen message the victim can be lured into "signing." This requires social engineering plus a bespoke puzzle, so likelihood is moderate rather than trivial, but the root cause — no enforcement of domain separation server-side — is squarely in scope and reachable by any RPC/dapp caller without special privilege.

### Recommendation
Do not allow the caller to disable domain separation on wallet message-signing endpoints. Remove (or ignore) the `safe_mode=False` raw-signing branches for `sign_message_by_address`/`sign_message_by_id`, and always apply the CHIP-0002 prefix (`Program.to((CHIP_0002_SIGN_MESSAGE_PREFIX, message))`) regardless of caller input, consistent with how the client-side fix in the referenced report removed the dapp-controlled `displayMessage` toggle.

### Proof of Concept
1. A dapp/RPC caller invokes `sign_message_by_address` (or `by_id`) with `message=<attacker-chosen bytes>`, `is_hex=True`, `safe_mode=False` [10](#0-9) .
2. The wallet backend signs the raw bytes directly: `AugSchemeMPL.sign(secret_key, bytes.fromhex(message))` using the account's real synthetic private key [3](#0-2) [11](#0-10) .
3. The attacker crafts a coin/puzzle whose solution includes an `AGG_SIG_UNSAFE (pubkey, message)` condition using the same pubkey/message.
4. `pkm_pairs`/`pkm_pairs_for_conditions_dict` accept the pair as valid because the message does not end with a reserved suffix [1](#0-0) , letting the attacker submit the signature obtained in step 2 to authorize that coin's spend.

### Citations

**File:** chia/consensus/condition_tools.py (L99-106)
```python
def pkm_pairs(conditions: SpendBundleConditions, additional_data: bytes) -> tuple[list[G1Element], list[bytes]]:
    ret: tuple[list[G1Element], list[bytes]] = ([], [])

    data = agg_sig_additional_data(additional_data)

    for pk, msg in conditions.agg_sig_unsafe:
        ret[0].append(pk)
        ret[1].append(msg)
```

**File:** chia/consensus/condition_tools.py (L140-145)
```python
    for cwa in conditions_dict.get(ConditionOpcode.AGG_SIG_UNSAFE, []):
        validate_cwa(cwa)
        for disallowed in data.values():
            if cwa.vars[1].endswith(disallowed):
                raise ConsensusError(Err.INVALID_CONDITION)
        ret.append((G1Element.from_bytes(cwa.vars[0]), cwa.vars[1]))
```

**File:** chia/wallet/util/signing.py (L72-85)
```python
def sign_message(secret_key: PrivateKey, message: str, mode: SigningMode) -> SignMessageResponse:
    public_key = secret_key.get_g1()
    if mode == SigningMode.CHIP_0002_HEX_INPUT:
        hex_message: bytes = Program.to((CHIP_0002_SIGN_MESSAGE_PREFIX, bytes.fromhex(message))).get_tree_hash()
    elif mode == SigningMode.BLS_MESSAGE_AUGMENTATION_UTF8_INPUT:
        hex_message = bytes(message, "utf-8")
    elif mode == SigningMode.BLS_MESSAGE_AUGMENTATION_HEX_INPUT:
        hex_message = bytes.fromhex(message)
    else:
        hex_message = Program.to((CHIP_0002_SIGN_MESSAGE_PREFIX, message)).get_tree_hash()
    return SignMessageResponse(
        pubkey=public_key,
        signature=AugSchemeMPL.sign(secret_key, hex_message),
    )
```

**File:** chia/wallet/wallet_request_types.py (L422-465)
```python
def signing_mode_enum(request: SignMessageByAddress | SignMessageByID) -> SigningMode:
    if request.is_hex and request.safe_mode:
        return SigningMode.CHIP_0002_HEX_INPUT
    elif not request.is_hex and not request.safe_mode:
        return SigningMode.BLS_MESSAGE_AUGMENTATION_UTF8_INPUT
    elif request.is_hex and not request.safe_mode:
        return SigningMode.BLS_MESSAGE_AUGMENTATION_HEX_INPUT

    return SigningMode.CHIP_0002


@streamable
@dataclass(kw_only=True, frozen=True)
class SignMessageByAddress(Streamable):
    address: str
    message: str
    is_hex: bool = False
    safe_mode: bool = True

    @property
    def signing_mode_enum(self) -> SigningMode:
        return signing_mode_enum(self)


@streamable
@dataclass(kw_only=True, frozen=True)
class SignMessageByAddressResponse(Streamable):
    pubkey: G1Element
    signature: G2Element
    signing_mode: str


@streamable
@dataclass(kw_only=True, frozen=True)
class SignMessageByID(Streamable):
    id: str
    message: str
    is_hex: bool = False
    safe_mode: bool = True

    @property
    def signing_mode_enum(self) -> SigningMode:
        return signing_mode_enum(self)

```

**File:** chia/wallet/wallet_rpc_api.py (L1762-1780)
```python
    async def sign_message_by_address(self, request: SignMessageByAddress) -> SignMessageByAddressResponse:
        """
        Given a derived P2 address, sign the message by its private key.
        :param request:
        :return:
        """
        synthetic_secret_key = self.service.wallet_state_manager.main_wallet.convert_secret_key_to_synthetic(
            await self.service.wallet_state_manager.get_private_key(decode_puzzle_hash(request.address))
        )
        signing_response = sign_message(
            secret_key=synthetic_secret_key,
            message=request.message,
            mode=request.signing_mode_enum,
        )
        return SignMessageByAddressResponse(
            pubkey=signing_response.pubkey,
            signature=signing_response.signature,
            signing_mode=request.signing_mode_enum.value,
        )
```

**File:** chia/types/signing_mode.py (L10-15)
```python
    # CHIP-0002 signs the result of sha256tree(cons("Chia Signed Message", message)) using the
    # BLS message augmentation scheme
    CHIP_0002 = "BLS_SIG_BLS12381G2_XMD:SHA-256_SSWU_RO_AUG:CHIP-0002_"

    # Same as above but with the message specified as a string of hex characters
    CHIP_0002_HEX_INPUT = "BLS_SIG_BLS12381G2_XMD:SHA-256_SSWU_RO_AUG:CHIP-0002_HEX"
```
