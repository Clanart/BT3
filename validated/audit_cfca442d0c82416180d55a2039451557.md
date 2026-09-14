## Analog Found

### Title
Signature-Domain Confusion in Wallet Message Signing Allows Coin-Theft via `sign_message_by_address`/`sign_message_by_id` — (File: chia/wallet/util/signing.py)

### Summary
The jose4j advisory is fundamentally about a verifier/decryptor not cryptographically binding an operation to the *purpose* the caller intends, so the same primitive (RSA decryption) can be redirected by an attacker to a dangerous alternate use (a padding oracle). Chia's wallet message-signing RPCs have the analogous defect: they let a caller obtain a raw BLS signature from the *same private key* that authorizes real coin spends (`AGG_SIG_ME`), over attacker-chosen bytes, with no cryptographic domain separation from an actual spend-authorization message when `safe_mode=False`.

### Finding Description
`sign_message` in [1](#0-0)  signs the caller-supplied `message` directly with the wallet's real spend-authorizing secret key:

- `BLS_MESSAGE_AUGMENTATION_HEX_INPUT` mode signs the raw hex bytes verbatim (`hex_message = bytes.fromhex(message)`), with **no domain prefix**.
- This mode is selected whenever `is_hex=True, safe_mode=False`, as defined in `signing_mode_enum()`: [2](#0-1) .

The RPC handler `sign_message_by_address` fetches the exact same `synthetic_secret_key` used for on-chain spend authorization (`convert_secret_key_to_synthetic` / `calculate_synthetic_secret_key`) and signs the caller's raw bytes with it: [3](#0-2) .

That same synthetic secret key is precisely what standard-puzzle coin spends use to satisfy the `AGG_SIG_ME` condition, whose message format is `delegated_puzzle_hash + coin_name + AGG_SIG_ME_ADDITIONAL_DATA` — confirmed directly in the test suite: [4](#0-3) . The puzzle itself is the standard `p2_delegated_puzzle_or_hidden_puzzle` (the "standard coin" puzzle) described in [5](#0-4) .

Because `delegated_puzzle_hash`, `coin_name` and `AGG_SIG_ME_ADDITIONAL_DATA` are all public/derivable (the puzzle hash of any XCH address is public, the additional data is a network constant, and coin names of a wallet's UTXOs are visible on-chain), an attacker can:
1. Pick a target coin belonging to the victim's address and compute `coin_name`.
2. Construct an attacker-chosen delegated puzzle (e.g., one unconditionally paying the coin to the attacker) and compute `delegated_puzzle_hash`.
3. Concatenate `delegated_puzzle_hash + coin_name + AGG_SIG_ME_ADDITIONAL_DATA` and hex-encode it as an innocuous-looking "message" (e.g., presented as a login/verification challenge by a malicious dApp/WalletConnect-style integration).
4. Trick the victim into calling `sign_message_by_address` (or the CLI `chia wallet sign_message`) with `is_hex=True, safe_mode=False` on that string.
5. The wallet returns `AugSchemeMPL.sign(secret_key, raw_bytes)` — which is a fully valid `AGG_SIG_ME` signature for spending that coin with the attacker's delegated puzzle.
6. The attacker submits a spend bundle revealing that delegated puzzle/solution with the obtained signature; the coin is spent to the attacker with no further wallet interaction.

The CHIP-0002 signing modes were introduced precisely to prevent this class of confusion by hashing the message inside a `("Chia Signed Message", message)` tree structure before signing (`get_tree_hash()` in [6](#0-5) ), guaranteeing the signed payload can never collide with a real spend-bundle `AGG_SIG_ME` preimage. But the raw `BLS_MESSAGE_AUGMENTATION_*` modes, still reachable and using the *real spending key*, lack this separation — the same structural weakness as jose4j accepting `RSA1_5` semantics for a key/ciphertext an attacker chose to place in a different (dangerous) context.

### Impact Explanation
Successful exploitation results in **unauthorized/unsigned coin movement**: full theft of the specific coin whose id the attacker targeted, using only a signature the victim believed was for an unrelated "sign this message" action (e.g., a login/verification flow). This maps directly to the required impact category "concrete unsigned or unauthorized coin movement."

### Likelihood Explanation
Exploitation requires the victim (a wallet user or local RPC caller of a dApp/tool) to sign attacker-supplied hex data with `safe_mode=False`. This is not the CLI default (`SignMessageCMD` calls the RPC with defaults, and `safe_mode` defaults to `True` in `SignMessageByAddress`/`SignMessageByID`), but any third-party integration or dApp bridge that lets a website/service request "sign this hex message" with `is_hex=True, safe_mode=False` (a supported, documented API surface — see `wallet/wallet_request_types.py` and the CHANGELOG entries introducing WalletConnect and these signing RPCs) exposes the flaw. The attacker needs only public information (address, expected coin name, additional data constant) to precompute the exact bytes to request. This is a Medium-likelihood, high-impact issue given it depends on social engineering plus an insecure signing-mode combination being exercised by client software.

### Recommendation
- Deprecate/remove or gate `BLS_MESSAGE_AUGMENTATION_HEX_INPUT`/`UTF8_INPUT` (`safe_mode=False`) signing paths that use the real spend-authorizing (synthetic) secret key; restrict raw/unprefixed signing to keys/derivation paths that are never used for `AGG_SIG_ME` coin authorization.
- Enforce `safe_mode=True` (CHIP-0002 domain-separated hashing) as the only option for `sign_message_by_address`/`sign_message_by_id`, or at minimum require explicit, loudly-surfaced user confirmation showing the raw bytes and their potential use as a valid `AGG_SIG_ME` preimage whenever `safe_mode=False` is requested.
- Add server-side validation rejecting raw-mode sign requests whose message bytes could parse as `puzzle_hash(32) + coin_name(32) + AGG_SIG_ME_ADDITIONAL_DATA(32)` for any coin controlled by the wallet.

### Proof of Concept
1. Attacker learns victim's XCH address/puzzle hash `PH` and observes an unspent coin owned by the victim with id `coin_name = sha256(parent_id || PH || amount)`.
2. Attacker builds a delegated puzzle `P = (q . ((51 attacker_ph amount)))` (an unconditional "pay to attacker" condition) and computes `dph = P.get_tree_hash()`.
3. Attacker computes `raw = dph + coin_name + AGG_SIG_ME_ADDITIONAL_DATA` (96 bytes) and hex-encodes it as `msg_hex`.
4. Attacker's front-end asks the victim's wallet to `sign_message_by_address(address=PH_address, message=msg_hex, is_hex=True, safe_mode=False)` (per [7](#0-6)  and handler at [3](#0-2) ), framed as an innocuous "verify ownership" login flow.
5. Wallet returns `signature = AugSchemeMPL.sign(synthetic_secret_key, raw)`.
6. Attacker assembles `CoinSpend(coin, puzzle_for_pk(pubkey), solution_for_delegated_puzzle(P, ()))` with `aggregated_signature=signature` and submits it as a `SpendBundle`; it validates against `AGG_SIG_ME` and moves the coin to the attacker with no further victim interaction.

### Citations

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

**File:** chia/wallet/wallet_request_types.py (L422-430)
```python
def signing_mode_enum(request: SignMessageByAddress | SignMessageByID) -> SigningMode:
    if request.is_hex and request.safe_mode:
        return SigningMode.CHIP_0002_HEX_INPUT
    elif not request.is_hex and not request.safe_mode:
        return SigningMode.BLS_MESSAGE_AUGMENTATION_UTF8_INPUT
    elif request.is_hex and not request.safe_mode:
        return SigningMode.BLS_MESSAGE_AUGMENTATION_HEX_INPUT

    return SigningMode.CHIP_0002
```

**File:** chia/wallet/wallet_request_types.py (L433-443)
```python
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

**File:** chia/_tests/wallet/test_signer_protocol.py (L156-157)
```python
    synthetic_pubkey: G1Element = G1Element.from_bytes(atom)
    message: bytes = delegated_puzzle_hash + coin.name() + wallet_state_manager.constants.AGG_SIG_ME_ADDITIONAL_DATA
```

**File:** chia/wallet/puzzles/p2_delegated_puzzle_or_hidden_puzzle.py (L17-56)
```python

p2_delegated_puzzle_or_hidden_puzzle is essentially the "standard coin" in chia.
DEFAULT_HIDDEN_PUZZLE_HASH from this puzzle is used with
calculate_synthetic_secret_key in the wallet's standard pk_to_sk finder.

This is important because it allows sign_coin_spends to function properly via the
following mechanism:

- A 'standard coin' coin exists in the blockchain with some puzzle hash.

- The user's wallet contains a primary sk/pk pair which are used to derive to one
  level a set of auxiliary sk/pk pairs which are used for specific coins. These
  can be used for signing in AGG_SIG_ME, but the standard coin uses a key further
  derived from one of these via calculate_synthetic_secret_key as described in
  https://chialisp.com/docs/standard_transaction. Therefore, when a wallet needs
  to find a secret key for signing based on a public key, it needs to try repeating
  this derivation as well and see if the G1Element (pk) associated with any of the
  derived secret keys matches the pk requested by the coin.

- Python code previously appeared which was written like:

    delegated_puzzle_solution = Program.to((1, condition_args))
    solutions = Program.to([[], delgated_puzzle_solution, []])

  In context, delegated_puzzle_solution here is any *chialisp program*, here one
  simply quoting a list of conditions, and the following argument is the arguments
  to this program, which here are unused. Secondly, the actual arguments to the
  p2_delegated_puzzle_or_hidden_puzzle are given. The first argument determines
  whether a hidden or revealed puzzle is used. If the puzzle is hidden, then what
  is required is a signature given a specific synthetic key since the key cannot be
  derived inline without the puzzle. In that case, the first argument is this key.
  In most cases, the puzzle will be revealed, and this argument will be the nil object,
  () (represented here by an empty python list).

  The second and third arguments are a chialisp program and its corresponding
  arguments, which will be run inside the standard coin puzzle. This interacts with
  sign_coin_spend in that the AGG_SIG_ME condition added by the inner puzzle asks the
  surrounding system to provide a signature over the provided program with a synthetic
  key whose derivation is within. Any wallets which intend to use standard coins in
  this way must try to resolve a public key to a secret key via this derivation.
```
