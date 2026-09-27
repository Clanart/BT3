import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 10
# todo: the path from https://github.com/serai-dex/serai
SOURCE_REPO = "serai-dex/serai"
# todo: the name of the repository
REPO_NAME = "serai"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    # =================================================================================
    # modular-frost signing core: preprocess/nonce caching, binding factors, rho transcript,
    # signing-set validation, share aggregation and blame
    # =================================================================================
    "crypto/frost/src/sign.rs",
    "crypto/frost/src/nonce.rs",
    "crypto/frost/src/algorithm.rs",
    "crypto/frost/src/lib.rs",

    # =================================================================================
    # modular-frost curves: H1-H5 hashing, nonce derivation, identity-rejecting point reads
    # =================================================================================
    "crypto/frost/src/curve/mod.rs",
    "crypto/frost/src/curve/dalek.rs",
    "crypto/frost/src/curve/ed448.rs",
    "crypto/frost/src/curve/kp256.rs",

    # =================================================================================
    # dkg: ThresholdKeys/ThresholdView, Lagrange and constant interpolation, scale/offset,
    # key (de)serialization
    # =================================================================================
    "crypto/dkg/src/lib.rs",

    # =================================================================================
    # dkg-pedpop: commitments + PoK, secret share distribution, ECDH encryption, blame
    # =================================================================================
    "crypto/dkg/pedpop/src/lib.rs",
    "crypto/dkg/pedpop/src/encryption.rs",

    # =================================================================================
    # dkg-musig, generator promotion, key recovery and trusted-dealer key generation
    # =================================================================================
    "crypto/dkg/musig/src/lib.rs",
    "crypto/dkg/promote/src/lib.rs",
    "crypto/dkg/recovery/src/lib.rs",
    "crypto/dkg/dealer/src/lib.rs",

    # =================================================================================
    # DLEq proofs used by PedPoP blame and generator promotion (cross-group is excluded)
    # =================================================================================
    "crypto/dleq/src/lib.rs",

    # =================================================================================
    # schnorr-signatures and frost-schnorrkel: single, batch and half-aggregate verification
    # =================================================================================
    "crypto/schnorr/src/lib.rs",
    "crypto/schnorr/src/aggregate.rs",
    "crypto/schnorrkel/src/lib.rs",

    # =================================================================================
    # multiexp: Straus/Pippenger kernels and the randomized BatchVerifier with blame
    # =================================================================================
    "crypto/multiexp/src/lib.rs",
    "crypto/multiexp/src/batch.rs",
    "crypto/multiexp/src/straus.rs",
    "crypto/multiexp/src/pippenger.rs",

    # =================================================================================
    # flexible-transcript: DigestTranscript framing and Merlin wrapper
    # =================================================================================
    "crypto/transcript/src/lib.rs",
    "crypto/transcript/src/merlin.rs",

    # =================================================================================
    # ciphersuite: canonical read_F/read_G, hash_to_F for secp256k1/P-256
    # =================================================================================
    "crypto/ciphersuite/src/lib.rs",
    "crypto/ciphersuite/kp256/src/lib.rs",

    # =================================================================================
    # dalek-ff-group: Ristretto/Ed25519 scalar, field and point wrappers, torsion checks
    # =================================================================================
    "crypto/dalek-ff-group/src/lib.rs",
    "crypto/dalek-ff-group/src/field.rs",
    "crypto/dalek-ff-group/src/ciphersuite.rs",

    # =================================================================================
    # minimal-ed448 (a modular-frost dependency): field backend, point decoding, ciphersuite
    # =================================================================================
    "crypto/ed448/src/backend.rs",
    "crypto/ed448/src/field.rs",
    "crypto/ed448/src/scalar.rs",
    "crypto/ed448/src/point.rs",
    "crypto/ed448/src/ciphersuite.rs",

    # =================================================================================
    # bitcoin-serai: BIP-340 FROST algorithm, Taproot tweak, output scanner, transaction
    # construction and multisig signing
    # =================================================================================
    "networks/bitcoin/src/crypto.rs",
    "networks/bitcoin/src/wallet/mod.rs",
    "networks/bitcoin/src/wallet/send.rs",
]


target_scopes = [
    "Critical. Signing of unintended messages: an attacker who only controls untrusted bytes reaching AlgorithmSignMachine::read_preprocess / sign (Commitments::read, NonceCommitments::read, Curve::read_G) and the message or transaction data being signed gets an honest signer's SignatureShare that completes a valid signature over a different message, group key or signing set - because BindingFactor::calculate_binding_factors, the FROST_rho transcript (group_key, C::hash_msg, C::hash_commitments over the 'preprocesses' challenge), the per-participant transcript in sign, validate_map or the included sort/duplicate/OOB checks fail to bind every commitment, participant index, addendum and ThresholdView offset/scalar into rho and the challenge.",
    "Critical. Recovery of a private key share: a public output of the signing protocol (Preprocess, SignatureShare, a completed signature, or FrostError::InvalidShare blame) leaks enough to solve for a secret share - via nonce reuse or correlation in Curve::random_nonce (seed || secret repr, zero-rejection loop), seeded_preprocess / from_cache re-deriving the same Nonce from one CachedPreprocess across two messages, the base + rho * actual nonce combination in sign, ThresholdKeys::view applying the offset only to included[0] and scaling interpolation factors, or bitcoin-serai Hram negating c for odd R and verify negating s - reachable without the victim misusing a documented MUST.",
    "Critical. Ability to forge proofs or signatures: a verifier accepts a SchnorrSignature, SchnorrAggregate, frost-schnorrkel signature, DLEqProof, MultiDLEqProof or BatchVerifier batch the attacker produced without the secret - through batch_statements sign errors, weight() wide-reduction or challenge() byte handling in aggregate.rs / dleq, SchnorrAggregate::verify length or ordering assumptions, BatchVerifier::queue giving the first statement weight ONE, blame_vartime binary search, or Straus/Pippenger (prep_bits, algorithm() window thresholds) returning identity for a non-zero sum.",
    "Critical. Point and scalar decoding lets forged inputs through: Ciphersuite::read_F / read_G canonicity (to_bytes round-trip), Curve::read_G identity rejection, dalek-ff-group from_bytes torsion and decompress handling, minimal-ed448 Point::from_bytes (sign bit, negative zero, recover_x, is_torsion_free) and FieldElement::from_repr (byte 56, MODULUS), or kp256 hash_to_F reduction accept a non-canonical, small-order, identity or out-of-range encoding, so two encodings verify as one key/nonce or a torsion component makes a proof, signature or DKG commitment verify for the wrong statement.",
    "Critical. PedPoP DKG yields a group key the attacker controls or learns a share of: verify_r1's proof-of-knowledge challenge (context, participant, R, cached_msg) or Commitments::read accepting identity or reused commitments, KeyMachine::calculate_share share verification via share_verification_statements / exponential, the stripes-based verification shares and ThresholdKeys::new deriving group_key from only participants 1..=t, or the ECDH encryption layer (cipher with a static IV, per-message key, EncryptedMessage PoP via pop_challenge, EncryptionKeyProof DLEq) let crafted round messages bias the key, pass an invalid share, or make an honest party's blame proof decrypt someone else's secret share.",
    "Critical. MuSig, generator promotion, recovery or dealer keys are not what they claim: dkg-musig's check_keys (duplicate detection by encoding, identity keys), binding_factor_transcript (context, keys_len, ordered keys) and Interpolation::Constant binding factors, GeneratorPromotion::complete's DLEq transcript (group key, participant) and its proofs map handling, recover_key's parameter consistency check, or ThresholdKeys::read / write round-trip let an attacker-chosen public key cancel honest keys, reuse a proof across participants or generators, or produce ThresholdKeys whose group_key does not match the verification shares.",
    "Critical. Reportedly received funds which weren't actually received or spendable: bitcoin-serai's Scanner::new, register_offset, scan_transaction and scan_block, p2tr_script_buf with dangerous_assume_tweaked, tweak_keys' TapTweakHash offset and parity negation, and ReceivedOutput::read / write let a Bitcoin sender create outputs that are reported as a ReceivedOutput (wrong offset, wrong key, wrong value or outpoint, a script-path-spendable key, an immature coinbase through scan_transaction, or an output that decodes differently after a write/read round-trip) that the multisig cannot actually spend via key path.",
    "Critical. bitcoin-serai signs an unintended Bitcoin transaction: SignableTransaction::new (dust, fee_per_vbyte, calculate_weight_vbytes, change and OP_RETURN handling, NotEnoughFunds and weight checks), multisig's per-input p2tr_script_buf(group_key + offset) == prevout check, TransactionMachine::sign's taproot_key_spend_signature_hash with Prevouts::All, per-input commitments re-keying, and TransactionSignatureMachine::complete let attacker-chosen payments, received outputs or data produce a signed transaction that pays different amounts, burns funds to fees, spends an input under the wrong key, or yields a witness that is valid for a different sighash.",
    "High. Incorrect or incomplete cryptographic formulae in a verifier's callstack: arithmetic in dalek-ff-group (FieldElement sqrt, sqrt_ratio_i, pow, from_uniform_bytes, Scalar from_bytes_mod_order_wide), minimal-ed448's backend macros (invert, sqrt, sqrt_ratio, from_repr) and point add/double/to_bytes, multiexp kernels, or the DigestTranscript / MerlinTranscript encoding (member tags, length prefixes, the reserved dom-sep label, challenge forking) and Curve::hash / hash_to_F concatenation give a wrong result for attacker-chosen inputs, causing a verifier to accept or reject the wrong statement or two distinct transcripts to collide.",
    "Critical/High blind spot. An unprivileged party abuses an assumption these libraries never enforce: a public API (ThresholdKeys::new with Constant interpolation of the wrong length, view with an unsorted or foreign included set, AdditionalBlameMachine, Schnorr verify called before sign_share, IetfSchnorr with offsets, frost-schnorrkel's length-prefixed context in SchnorrkelHram, TransactionMachine's per-input HashMap re-keying) accepting input that silently breaks message binding, a secret leaking through a side-channel reachable from public data (vartime multiexp or blame on secret-dependent values, Zeroize gaps in Encryption / KeyMachine / AlgorithmSignMachine), a domain separator or context shared between two protocols so a proof, PoK or signature from one verifies in another, or a Bitcoin consensus edge case (duplicate txid, witness malleability, parity of R or tweaked key) the wallet code never considered - yielding a forged signature or proof, a recovered key share, a signature over an unintended message, or funds reported received that are not spendable.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one Serai crypto target.

    ```
    target_file format:
    "'File Name: crypto/frost/src/sign.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact Serai target:

    {target_file}

    Project focus:
    Serai secures cross-chain funds with threshold keys. The in-scope libraries are modular-frost (two-round FROST signing: preprocess commitments, rho binding factors, shares, blame), dkg / dkg-pedpop / dkg-musig / promote / recovery / dealer (key generation producing ThresholdKeys), schnorr-signatures (single, batch, half-aggregate), frost-schnorrkel, multiexp (BatchVerifier), flexible-transcript, dleq, ciphersuite / dalek-ff-group / minimal-ed448 / kp256 (encodings, hash_to_F), and bitcoin-serai (BIP-340 FROST, Taproot tweak, Scanner, SignableTransaction).

    Rules:
    * Treat `File Name:` as the exact file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible. Do not ask for code or say anything is missing.
    * Use exact Rust symbols (crate, struct, trait, fn, enum variant, const) when possible.
    * Attacker is unprivileged: holds no threshold of key shares and no other party's secret. They control only public inputs: messages or transaction data they cause to be signed, Bitcoin transactions and outputs they send, and untrusted bytes (points, scalars, signatures, proofs, keys, encrypted messages, preprocess or share encodings) that reach a public read / verify / sign API.
    * Never assume a malicious validator, node or network peer, a colluding threshold (signature production by threshold is out of scope), broken BFT assumptions, a malicious RPC node, leaked keys, unsafe code, test code, or invalid hashes/curves/ciphersuites supplied by the integrator.
    * Out of scope: the experimental cross-group DLEq (dleq/src/cross_group), coordinator, processor, substrate, message-queue, orchestration, and anything already in GitHub issues or the audits folder.
    * Ignore test files, ff-group-tests, docs, Cargo/config, and misuse of a documented MUST (reusing a CachedPreprocess, unsecure register_offset offsets, vartime APIs on secrets).
    * Every question must be a real scenario: name the public function called, the exact crafted input (bytes, point, scalar, message, tx), the state it relies on, and the broken invariant. No unbounded-loop, memory-growth, huge-input or gas-style speculation.
    * Generate 40 to 80 high-signal questions. At least 70% must target signing of unintended messages, key share recovery, forged proofs/signatures, or funds reported received that are not spendable.
    * Every question must be testable with a `cargo test` unit or property test against the target crate.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Unforgeability: a verifier accepts a signature or proof only if it was produced with the secret for that exact key and statement.
    * Message binding: an honest SignatureShare is usable only for the exact group key, message, signing set and commitments it was computed over.
    * Share secrecy: no public output (preprocess, share, signature, blame proof, encrypted message) lets anyone recover a secret or key share.
    * Key soundness: DKG / MuSig / promotion output ThresholdKeys whose group_key and verification shares are consistent and not attacker-controlled.
    * Encoding soundness: read_F / read_G / from_bytes accept only canonical, torsion-free (and, where required, non-identity) encodings.
    * Receipt soundness: Scanner reports only outputs that exist in the transaction and are key-path spendable by key + offset.

    Each question must include:
    1. target function/method;
    2. attacker action (the crafted input and the API it reaches);
    3. preconditions (key/params state, signing set, registered offsets, prior messages);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_INPUT under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: cargo test PARAMETERS and assert UNFORGEABILITY, MESSAGE_BINDING, SHARE_SECRECY, KEY_SOUNDNESS, ENCODING_SOUNDNESS, or RECEIPT_SOUNDNESS.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused Serai crypto exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged: no threshold of key shares, no other party's secret. They control only messages / transaction data they cause to be signed, Bitcoin transactions they send, and untrusted bytes reaching a public read / verify / sign API.
- Reject malicious-validator, malicious-node, network-peer, colluding-threshold, broken-BFT, malicious-RPC, leaked-key, unsafe-code and integrator-supplied invalid hash/curve/ciphersuite paths.
- Reject the experimental cross-group DLEq, coordinator/processor/substrate code, test code, docs and config, and misuse of a documented MUST (CachedPreprocess reuse, insecure register_offset offsets, vartime APIs on secrets).
- Reject generic unbounded-loop or memory claims with no concrete input and no broken invariant.
- Focus on real impact: signing an unintended message, recovering a key share, forging a proof or signature, or funds reported received that are not spendable.

## Validate
- Trace the exact path from the attacker-controlled input into the affected function.
- Check existing guards: read_F / read_G canonicity, Curve::read_G identity rejection, torsion checks, validate_map and signing-set checks in sign, the FROST_rho transcript, verify / verify_share in complete, BatchVerifier random weights, PedPoP PoK and share verification, DLEq transcripts, musig check_keys, and bitcoin-serai's p2tr_script_buf / multisig prevout check.
- Accept only concrete forgery, key share recovery, unintended signing, or false receipt.
- Require exact file/function support and a reproducible `cargo test` PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker input, exploit flow, and why existing guards fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (signing of unintended messages, forged proofs, key share recovery, funds reported received that were not received/spendable) or High (incorrect cryptographic formulae in a verifier's callstack)]

### Likelihood Explanation
[Attacker capability, required inputs and state, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[cargo test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for Serai's crypto libraries.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production code only: crypto/frost, crypto/dkg (+ pedpop, musig, promote, recovery, dealer), crypto/dleq (not cross_group), crypto/schnorr, crypto/schnorrkel, crypto/multiexp, crypto/transcript, crypto/ciphersuite (+ kp256), crypto/dalek-ff-group, crypto/ed448, networks/bitcoin/src (crypto.rs, wallet/). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof. The analog must stand on Serai's own code.
- Keep only analogs an unprivileged party can reach with public inputs: messages or transaction data they cause to be signed, Bitcoin transactions they send, and untrusted bytes fed to read_F / read_G / read_preprocess / read_share / Commitments::read / EncryptedMessage::read / DLEqProof::read / SchnorrSignature::read / ThresholdKeys::read / ReceivedOutput::read or to a verify / sign / calculate_share / complete API.
- Map the class onto Serai's real shape, where its bugs live:
  * FROST binding: rho over group_key, hash_msg and the 'preprocesses' challenge; per-participant transcript; addendum; ThresholdView scalar/offset (offset added to included[0]); parallel-session / ROS-style share reuse; CachedPreprocess determinism;
  * nonce derivation: Curve::random_nonce hashing seed || secret, base + rho * actual combination;
  * Lagrange / Constant interpolation, participant indexes (zero, duplicate, > n, unsorted), group_key from participants 1..=t in ThresholdKeys::new;
  * PedPoP: PoK challenge context/participant binding, identity or reused commitments, share verification, ECDH with static IV, per-message PoP, blame proofs revealing ECDH keys;
  * MuSig rogue-key and duplicate-encoding checks, generator promotion DLEq binding, key recovery;
  * verification math: Schnorr batch_statements, half-aggregation weight(), DLEq challenge wide reduction, BatchVerifier first-weight ONE and blame search, Straus/Pippenger edge cases;
  * encodings: non-canonical scalars/points, torsion, negative zero, identity, hash_to_F bias, naive dst || msg concatenation, transcript framing and domain-separator reuse;
  * bitcoin-serai: BIP-340 parity/negation of R and key, TapTweak, Scanner matching script_pubkey only, coinbase maturity, register_offset collisions, fee/change/dust math, sighash with Prevouts::All, per-input commitment re-keying.
- Reject malicious-validator/node/peer, colluding-threshold, broken-BFT, malicious-RPC, leaked-key, unsafe-code, integrator-supplied invalid curve/hash, cross-group DLEq, test-only, documented-MUST misuse, and no-impact analogs.
- Critical, High and Medium only; no low, informational, best-practice or timing-only analogs without secret leakage.

## Validate
- Map the bug class to the strongest reachable path from public inputs, naming the exact functions and bytes.
- Prove root cause with exact file/function support in the in-scope crates.
- Accept only concrete signing of an unintended message, key share recovery, a forged proof or signature, funds reported received that are not spendable, an incorrect verifier formula, or an undocumented transcript collision.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for Serai security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and RESEARCHER.md for scope, exclusions, and valid impact classes.
- Scope (Immunefi Serai program) is the crates ciphersuite (+ kp256), dkg (+ pedpop, promote, recovery, dealer), dkg-musig, modular-frost, frost-schnorrkel, schnorr-signatures, multiexp, flexible-transcript, dalek-ff-group, bitcoin-serai, plus dleq and minimal-ed448 only as dependencies reached from them (Primacy of Impact). The coordinator, processor, substrate, message-queue and orchestration are out of scope.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Accepted impacts only:
  * Critical: signing of unintended messages; ability to forge proofs; unintended, undocumented recovery of private spend keys or key shares; reportedly received funds which weren't actually received/spendable.
  * High: incorrect/incomplete (in the academic sense) cryptographic formulae within a verifier's callstack.
  * Medium: undocumented transcript collision.
  * Low: undocumented panic reachable from a public API; non-constant-time implementation with regard to secret data; incorrect/incomplete cryptographic formulae within a prover's callstack.
- Reject attacks breaking BFT assumptions, signature production by a threshold, malicious validators/nodes/peers, attacks on out-of-scope communication protocols, invalid hashes/curves/ciphersuites, the experimental cross-group DLEq proof, test code, bugs only reachable via unsafe code, leaked keys, centralization, Sybil, liquidity, best-practice critiques, and anything tested on mainnet or a public testnet.
- Treat as documented, not findings on their own: CachedPreprocess reuse leaking the share, a zero binding factor, Ristretto hash_to_F dst/data transposition, IetfSchnorr losing compatibility with offsets, AdditionalBlameMachine::new assuming validated commitments, register_offset requiring secure offsets, scan_block including immature coinbase outputs, TransactionMachine panicking on cache / from_cache / non-empty message, Schnorr verify / verify_share before sign_share, bitcoin Hram panicking on infinity, vartime APIs used on secrets, and documented panics (original_verification_share on a bad index, SchnorrAggregate::write over 4B signatures, Decryption::register re-registration).
- Reject anything already in GitHub issues or the audits folder, including unfixed items in the Cypher Stack March 2023 crypto and August 2023 bitcoin audits.
- A PoC is mandatory for Critical and for panic reports; prose alone is not accepted. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and a broken unforgeability, message-binding, share-secrecy, key-soundness, encoding-soundness or receipt-soundness invariant.
3. Reachable path: attacker-controlled public input (message/tx data, Bitcoin transaction, or untrusted bytes into a public read / verify / sign API) -> trigger -> bad result, with no threshold of shares and no other party's secret.
4. Existing guards reviewed and shown insufficient: read_F / read_G canonicity, Curve::read_G identity rejection, torsion checks, validate_map and signing-set checks, the FROST_rho transcript, verify / verify_share in complete, BatchVerifier random weights, PedPoP PoK and share checks, DLEq transcripts, musig check_keys, p2tr_script_buf and the multisig prevout check.
5. Concrete impact matching one accepted category above, with realistic likelihood.
6. Reproducible proof path: a `cargo test` PoC against the affected crate on a local setup.
7. No rejection reason from SECURITY.md, the documented behaviours above, privilege assumptions, or known issues.

## Silent Triage Questions
Before output, internally answer:
- Can a party with no threshold of shares and no other party's secret trigger this through public inputs only?
- Does the code actually behave as claimed, not just under a documented MUST violation or a malicious integrator?
- Is the impact caused by the in-scope crates, not by the coordinator, processor, substrate, a node, or a colluding threshold?
- Is it beyond the documented behaviours and absent from GitHub issues and the audits folder?
- Is the forgery, key recovery, unintended signature, false receipt, formula error, collision or panic concrete rather than hypothetical?
- Would a triager accept the proof-of-concept, and what exact test proves it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing guards fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and the exact Immunefi Serai impact it maps to]

## Likelihood Explanation
[Attacker capability, inputs and state required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or a cargo test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
