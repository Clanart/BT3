import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'crate-crypto/go-eth-kzg'
# todo: the name of the repository
REPO_NAME = 'go-eth-kzg'

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


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
    # LENS: KZG VERIFIER SOUNDNESS AND SPEC CONFORMANCE (EIP-4844 / EIP-7594 PeerDAS).
    # go-eth-kzg is the KZG library consensus and execution clients call to decide
    # whether attacker-authored bytes - a Blob, a KZGCommitment, a KZGProof, a Cell, a
    # cell index, an evaluation point - are accepted. The files below sit on the path
    # from those bytes to one of four decisions: does Verify accept only a true opening,
    # does the batch accept only when every member would, does the deserialised element
    # equal a canonical subgroup element, and do cells / proofs / recovered data equal
    # what the consensus spec and c-kzg produce for the same input. A question belongs
    # here only if it can be closed by an equality between what the library accepts or
    # emits and what the spec says it must accept or emit.
    # =================================================================================
    # -- root package: the public Context API every client calls -----------------------
    # api.go builds the Context from the trusted setup; verify.go / prove.go are the
    # EIP-4844 entry points; api_eip7594.go is the PeerDAS cell prover, verifier and
    # recovery; api_eip.go is cell recovery for cell-level messaging.

    # -- root package: context, EIP-4844 and EIP-7594 entry points ------------------------
    "api.go",
    "api_eip.go",
    "api_eip7594.go",
    "verify.go",
    "prove.go",
    "errors.go",

    # -- root package: byte <-> field / group element boundary, transcript, setup --------
    "serialization.go",
    "fiatshamir.go",
    "trusted_setup.go",

    # -- internal/kzg: single-point opening, batch verification, commit key ----------------
    "internal/kzg/kzg.go",
    "internal/kzg/kzg_prove.go",
    "internal/kzg/kzg_verify.go",
    "internal/kzg/srs.go",
    "internal/kzg/errors.go",

    # -- internal/kzg_multi: multi-point (cell) batch verification, opening key, FK20 -------
    "internal/kzg_multi/kzg_prove.go",
    "internal/kzg_multi/kzg_verify.go",
    "internal/kzg_multi/srs.go",
    "internal/kzg_multi/errors.go",
    "internal/kzg_multi/fk20/fk20.go",
    "internal/kzg_multi/fk20/toeplitz.go",

    # -- internal/erasure_code: block-erasure recovery of the extended blob ----------------
    "internal/erasure_code/erasure_code.go",

    # -- internal/domain: roots of unity, bit reversal, Lagrange evaluation, FFTs ----------
    "internal/domain/domain.go",
    "internal/domain/fft.go",
    "internal/domain/coset_fft.go",
    "internal/domain/errors.go",

    # -- internal: multi-exponentiation, coefficient polynomials, scalar helpers -----------
    "internal/multiexp/multiexp.go",
    "internal/multiexp/errors.go",
    "internal/poly/poly.go",
    "internal/utils/utils.go",

    # =================================================================================
    # NOT AUDITED (excluded from every variant): every *_test.go and bench_*_test.go,
    # examples_test.go, consensus_specs_test.go, the tests/ fixture tree (data.yaml);
    # internal/kzg/srs_insecure.go (test-only SRS with a supplied secret, never used by
    # NewContext4096); trusted_setup.json (embedded ceremony output); go.mod / go.sum,
    # .golangci.yml, .github workflows and scripts, audits/, LICENSE and readme.md. A
    # defect in any of these is only in scope when it is reachable from the audited
    # code above through the public Context API.
    # =================================================================================
]


target_scopes = [
    "Critical. VERIFY MUST ACCEPT ONLY A TRUE OPENING. `kzg.Verify` checks e([f(α)-f(z)]G₁, -G₂) · e(Q, [α-z]G₂) == 1 after `Context.VerifyKZGProof` / `VerifyBlobKZGProof` deserialise `blobCommitment`, `kzgProof`, `inputPointBytes`, `claimedValueBytes` and `VerifyBlobKZGProof` derives `ClaimedValue` from the blob via `EvaluateLagrangePolynomial`. `deserializeG1Point` accepts the point at infinity (`PointAtInfinity`) for both commitment and proof; `SubAssign` on Jacobian points and `FromJacobian` normalise before `PairingCheck`. Probe every (commitment, proof, z, y) tuple that passes without f(z) == y: commitment or proof at infinity with a chosen y; z equal to a bit-reversed domain root so `FindRootIndex` returns an index and the value is read straight from the blob; z == α-independent degenerate cases where [α-z]G₂ is the identity; a blob whose polynomial is constant so every quotient is zero. Identity: `Verify` returns nil ⇔ the polynomial committed by `blobCommitment` evaluates to `claimedValue` at `inputPoint`, for every deserialisable input.",

    "Critical. THE BATCH MUST ACCEPT ONLY WHEN EVERY MEMBER WOULD. `kzg.BatchVerifyMultiPoints` folds `commitments`, `ClaimedValue` and `QuotientCommitment` with `utils.ComputePowers(randomNumber, batchSize)` drawn from `fr.SetRandom`, short-circuits `batchSize == 1` to `Verify`, and multiplies `randomNumbers[i]` by `InputPoint` in place for the second `MultiExp`; `Context.VerifyBlobKZGProofBatch` builds `openingProofs` per blob with `computeChallenge(blob, serComm)`; `VerifyBlobKZGProofBatchPar` runs `VerifyBlobKZGProof` per index under `errgroup`. Show a batch containing one invalid (blob, commitment, proof) triple that returns nil, or a batch of valid triples that fails: a batch where two members cancel in the folded pairing because their quotients or commitments are negations or infinity; a length mismatch between `commitments` and `proofs` caught late; a zero-length batch returning nil where a caller treats nil as 'all valid'; a `randomNumbers` power sequence that is not injective over the batch. Identity: `VerifyBlobKZGProofBatch(blobs, comms, proofs) == nil` ⇔ `VerifyBlobKZGProof(blobs[i], comms[i], proofs[i]) == nil` for every i, with probability 1 - negl.",

    "Critical. A CELL BATCH MUST BIND EACH CELL TO ITS COMMITMENT AND COSET. `Context.VerifyCellKZGProofBatch` runs `deduplicateKZGCommitments` to get `rowCommitments, rowIndices`, bounds-checks `cellIndices < CellsPerExtBlob`, deserialises, and calls `kzgmulti.VerifyMultiPointKZGProofBatch`, which sizes `rPowers` by `len(commitmentIndices)`, accumulates `weights[commitmentIndex]`, bit-reverses and coset-IFFTs each `cosetEval` in place through `openKey.cosetDomains[cosetIndex]`, sums them with `poly.PolyAdd` (which drops nothing but `removeTrailingZeros` elsewhere does), commits with `CommitG1`, scales proofs by `CosetShiftsPowCosetSize[cosetIndex]` and pairs against `openKey.G2[cosetSize]` and `genG2()`. Show a (commitments, cellIndices, cells, proofs) batch accepted although some cell is not the committed polynomial's evaluation on that coset: the same `cellIndex` repeated with two different cells whose contributions cancel; a cell of all zeros with a proof at infinity; a `rowIndices` / `cellIndices` pairing where the dedup map reorders commitments relative to `cellIndices`; a proof set where `rPowers[k]` and the weighted `CosetShiftsPowCosetSize` term collapse; an `interpolationPoly` shorter than `cosetSize` after `PolyAdd`. Identity: acceptance ⇔ for every k, `cells[k]` == evaluations of the polynomial committed by `commitments[k]` on coset `cellIndices[k]`, and `proofs[k]` opens exactly that.",

    "Critical. BYTES ACCEPTED MUST BE CANONICAL SUBGROUP ELEMENTS AND CANONICAL SCALARS. `deserializeG1Point` relies on gnark `SetBytes` for on-curve and subgroup checks and the `0xc0` infinity flag; `DeserializeScalar` and `deserializeBlobToPoly` use `SetBytesCanonical` / `ReduceCanonicalBigEndian` and return `ErrNonCanonicalScalar`; `deserializeCell` copies 32-byte chunks; `SerializeG1Point` / `SerializeScalar` round-trip. Probe every byte string that deserialises to an element the spec's `validate_kzg_g1` / `bytes_to_bls_field` would reject, or two byte strings that deserialise to the same element: a compressed point with the infinity bit set and non-zero x bytes; a point with both the `0x80` compression and sort flags in a combination gnark tolerates; a scalar equal to `BlsModulus`, `BlsModulus - 1` and `2^255`; a blob where one chunk is non-canonical but deserialisation happens after an earlier accept decision. Identity: `Deserialize*(b)` succeeds ⇔ b is the unique canonical encoding of an element of the prime-order subgroup (or of a scalar < `BlsModulus`), and `Serialize*(Deserialize*(b)) == b`.",

    "High. THE CHALLENGE MUST HASH EXACTLY THE SPEC TRANSCRIPT. `computeChallenge` writes `DomSepProtocol` (16 bytes), `u64ToByteArray16(ScalarsPerBlob)`, `blob[:]` and `commitment[:]` into SHA-256 then `challenge.SetBytes(digest)`; `ComputeBlobKZGProof` deserialises `blobCommitment` only for a subgroup check and never verifies it commits to `blob`; `VerifyBlobKZGProof` recomputes the challenge from the caller's `blobCommitment` bytes rather than the deserialised point. Show two distinct (blob, commitment) transcripts that yield one challenge, a challenge that differs from `compute_challenge` in the consensus spec for some blob so this client accepts what c-kzg rejects or vice versa, or a challenge landing in the domain so `EvaluateLagrangePolynomial` reads `poly[index]` and the quotient path changes: a `SetBytes` reduction mismatch with `hash_to_bls_field`, a commitment whose non-canonical but accepted encoding hashes differently from its canonical form, a blob with a non-canonical scalar hashed before rejection. Identity: `computeChallenge(blob, commitment)` == spec `compute_challenge(blob, commitment)` byte-for-byte, and equal challenges imply equal (blob, canonical commitment).",

    "High. THE EVALUATION AND QUOTIENT MUST EQUAL THE SPEC FOR EVERY z. `Domain.EvaluateLagrangePolynomialWithIndex` returns `&poly[index]` when `FindRootIndex` hits a (bit-reversed) root, else the barycentric formula with `getElementSlice` / `putElementSlice` pooled buffers and `fr.BatchInvert`; `kzg.Open` feeds `indexInDomain` into `computeQuotientPoly`, which selects `computeQuotientPolyOnDomain` (sets `rootsMinusZ[index]` to one, accumulates `q_m_j` with `PreComputedInverses[index]`) or `computeQuotientPolyOutsideDomain`. Show a (blob, z) where the returned `ClaimedValue` or `QuotientCommitment` differs from `evaluate_polynomial_in_evaluation_form` / `compute_kzg_proof_impl`, so a proof this library produces is rejected by c-kzg or a proof c-kzg produces is rejected here: z equal to a root under bit-reversed `Roots` versus natural order; a pooled `denom` slice carrying stale values into `BatchInvert`; `ClaimedValue` read from `&poly[index]` after `putPolynomial` returns the buffer to `polynomialPool`; a zero in `rootsMinusZ` silently skipped by `BatchInvert`. Identity: (`ClaimedValue`, `QuotientCommitment`) from `ComputeKZGProof(blob, z)` == spec output for (blob, z), and `VerifyKZGProof` accepts exactly the spec's accept set.",

    "High. POOLED BUFFERS MUST NEVER LEAK ONE CALL'S DATA INTO ANOTHER'S DECISION. `polynomialPool` hands out 4096-element `kzg.Polynomial` slices through `getPolynomial` / `putPolynomial` and `elementSlicePool` hands out up to 8192-element slices through `getElementSlice` / `putElementSlice`; `VerifyBlobKZGProofBatch` copies `claimedValue := *outputPoint` before `putPolynomial`; `EvaluateLagrangePolynomialWithIndex` defers `putElementSlice(invDenom)` on a slice `BatchInvert` freshly allocated; `ComputeCells` and `ComputeCellsAndKZGProofs` mutate the pooled `polynomial` in place via `BitReverse` and `IfftFr`; `VerifyBlobKZGProofBatchPar` verifies concurrently. Show any sequence of public calls - one attacker-authored input followed by an honest one, or two concurrent verifications - where an accept, reject, commitment, proof or cell depends on bytes from a previous call: a slice returned to the pool while a pointer into it is still read; `getElementSlice` returning a re-sliced buffer whose tail is not overwritten before use; a `Cell` or `KZGProof` array aliasing pooled memory after return. Identity: the result of every public `Context` method is a pure function of its arguments and the trusted setup, regardless of prior or concurrent calls.",

    "High. RECOVERED CELLS MUST EQUAL THE UNIQUE EXTENDED BLOB THE SUPPLIED CELLS LIE ON. `recoverPolynomialCoeffs` checks `len(cellIDs) == len(cells)`, `isAscending`, `cellID < CellsPerExtBlob` and `NumBlocksNeededToReconstruct`, bit-reverses missing ids, places cells at `cellID*scalarsPerCell`, bit-reverses the extended blob and calls `DataRecovery.RecoverPolynomialCoefficients`, which builds the vanishing polynomial on `rootsOfUnityBlockErasureIndex`, divides on the coset generated by `fr.NewElement(7)` with `BatchInvert`, and truncates to `numScalarsInDataWord` without checking the high coefficients are zero. `RecoverCellsAndComputeKZGProofs` then re-derives all 128 cells and proofs. Show 64 or more attacker-authored cells that are not evaluations of any degree < 4096 polynomial yet are accepted, so the recovered cells or proofs differ from the cells the node was given or from what c-kzg's `recover_cells_and_kzg_proofs` returns: inconsistent cells whose inconsistency is truncated away; a zero in `cosetZxEval`; supplied cells that do not survive recovery unchanged; an id set that satisfies `isAscending` but wraps `BitReverseInt`. Identity: for every supplied `cellIDs[i]`, `recovered[cellIDs[i]] == cells[i]`, and the recovered polynomial has degree < `ScalarsPerBlob`, else an error.",

    "High. CELLS AND PROOFS THIS LIBRARY EMITS MUST EQUAL C-KZG'S FOR THE SAME BLOB AND SETUP. `ComputeCellsAndKZGProofs` bit-reverses the blob, `IfftFr`s to coefficients, evaluates through `FK20.ComputeExtendedPolynomial` (`extDomain.FftFr`, `BitReverse`, `partition`) and proves through `ComputeMultiOpenProof` (`takeEveryNth`, `newToeplitz`, `embedCirculant`, `BatchMulAggregation`, `proofDomain.FftG1`, `BitReverse`); `NewFK20` reverses and truncates the monomial SRS and `padToPowerOfTwo`s it; `NewContext4096` slices `setupMonomialG1Points[:len(setupG2Points)]` for `openKey7594` and passes `scalarsPerCell` as coset size; `serializeCells` and `computeKZGProofsFromPolyCoeff` check only counts. Show a blob for which any emitted cell or proof differs from the consensus-spec `compute_cells_and_kzg_proofs`, or verifies under `VerifyCellKZGProofBatch` here yet fails in c-kzg (or the reverse), splitting clients on data availability: an off-by-one in `srs[evalSetSize:]`, a proof ordering after `BitReverse` that mismatches `cellIndices`, a Toeplitz row/column built from the wrong half of `polyCoeff`, a coset shift `extDomain.Roots[k*cosetSize]` taken after `BitReverse` versus before. Identity: (`cells`, `proofs`) == spec output for (blob, setup), and `VerifyCellKZGProofBatch` here agrees with c-kzg on every (commitment, index, cell, proof).",

    "Critical. THE MISSING INVARIANT - what nobody built. No check ties the `blobCommitment` passed to `ComputeBlobKZGProof` or `VerifyBlobKZGProof` to the blob beyond hashing its bytes; `deserializeG1Point` trusts gnark's notion of canonical encoding rather than the spec's; `VerifyCellKZGProofBatch` never rejects a duplicate `cellIndex` within one commitment; `RecoverPolynomialCoefficients` never asserts the recovered polynomial has degree < 4096; `NewContext4096` parses the setup with `NoSubgroupChecks` and never confirms `SetupG1Lagrange` is the IFFT of `SetupG1Monomial`; a zero-length batch returns nil. Identify the FIRST place one of these unstated soundness or conformance assumptions is violated by attacker-authored bytes reaching a public `Context` method, prove it with a `go test` that asserts both sides (accepted versus true opening, this library versus spec vectors in tests/, recovered versus supplied, batch versus per-member) before and after, and show that no later step in the client's verification pipeline can detect or reverse it.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate KZG verifier soundness / spec-conformance audit questions for one go-eth-kzg target.

    ```
    target_file format:
    "'File Name: internal/kzg/kzg_verify.go -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate cryptographic-library security audit questions for this exact go-eth-kzg
    target:

    {target_file}

    Project focus:
    go-eth-kzg is the KZG library Ethereum clients call for EIP-4844 blobs and EIP-7594
    PeerDAS cells. Untrusted bytes enter through the public `Context` methods: a `Blob`,
    a `KZGCommitment`, a `KZGProof`, a `Cell`, cell indices and evaluation points that an
    ordinary user authored and the protocol delivered to the verifier. The library decides
    (a) whether a single or batched opening proof is a true opening; (b) whether a cell
    batch binds each cell to its commitment and coset; (c) whether bytes deserialise to a
    canonical subgroup element or scalar; (d) whether cells, proofs and recovered data
    equal what the consensus spec and c-kzg produce. Anything accepted that is not a true
    opening, or any output that differs from the spec for the same input, is the bug.

    Rules:
    * Treat `File Name:` as the exact file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Go symbols (exported function, method, constant, error variable, struct
      field) as they appear in the file.
    * EVERY question must close on an equality that must hold across a call. State it
      explicitly. Narrative questions with no stated equality are rejected.
    * Attacker is unprivileged only: an ordinary Ethereum user who authors the blob,
      commitment, proof, cell, index or evaluation-point bytes that reach a public
      `Context` method through the normal protocol path, with their own keys and funds.
      They may call any public method with any arguments and order their own calls.
    * Attacker is NOT the trusted-setup ceremony, a client developer wiring the library
      wrongly, or an operator. No malicious peer, node or RPC assumption; no compromised
      dependency or machine; no social engineering.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - Tests, benchmarks, tests/ fixtures, srs_insecure.go, trusted_setup.json, go.mod,
        CI config, audits/ and readme are OUT OF SCOPE.
      - Denial of service, panics as DoS, timeouts, unbounded loops, allocation, cache
        growth and memory hygiene are OUT OF SCOPE.
      - Defects inside gnark-crypto with no path through this repo are OUT OF SCOPE; this
        repo relying on gnark for a check the spec requires and gnark not performing it
        is fully IN scope.
      - Also excluded: a dishonest trusted setup, side channels and timing, best-practice
        notes, feature requests, publicly known issues, and theoretical findings.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: a proof, blob or cell batch accepted that is not a true opening (forged
      data availability, consensus split); bytes deserialised to a non-canonical or
      out-of-subgroup element that then passes verification.
      High: this library and the consensus spec / c-kzg disagreeing on accept or on the
      emitted commitment, proof, cell or recovered data for the same input (client
      split); a valid proof rejected; pooled state from one call changing another's result.
    * Every question must be a concrete real-world scenario an unprivileged party can
      trigger through the public `Context` surface with bytes they authored.
    * A returned error is a finding only when the spec would accept, or when it replaces
      an accept the spec would reject - say which.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable locally with `go test` using the embedded trusted
      setup or the consensus-spec vectors in tests/. Never propose testing on mainnet or
      a public testnet.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name TWO values that must be equal and ask whether they are:
      accepted and true opening, batch and per-member, deserialised and canonical,
      challenge and spec transcript, emitted and spec output, recovered and supplied.

    Known dead ends - do NOT generate questions about these:
    * Anything needing the setup ceremony, a client developer or an operator to act
      maliciously.
    * A bug in gnark-crypto or the consensus spec itself with no path here.
    * DoS, panics, timeouts, memory, logging, or performance.
    * Findings only reproducible through tests or tooling.

    Core equalities (each question must close on one):
    * SOUNDNESS: `Verify` accepts ⇔ the committed polynomial opens to the claimed value.
    * BATCH == MEMBERS: batch accepts ⇔ every (commitment, proof, input) member accepts.
    * CELL BINDING: cell k accepted ⇔ it equals the committed polynomial on coset k.
    * CANONICAL BYTES: accepted bytes == unique canonical encoding of a subgroup element.
    * SPEC CONFORMANCE: emitted / accepted here == consensus spec and c-kzg for same input.
    * RECOVERY TRUTH: recovered cells at supplied ids == supplied cells, degree < 4096.

    Each question must include:
    1. target exported function, method or constant;
    2. attacker input (the concrete blob, commitment, proof, cell, index or scalar bytes
       that matter);
    3. preconditions (batch shape, duplicate indices, point at infinity, z in domain,
       pooled state);
    4. call sequence through the Context method, internal package and gnark call;
    5. the equality that breaks, written explicitly;
    6. scoped impact and which clients or chain state are exposed;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Method: function_name] Can an unprivileged ATTACKER_INPUT under PRECONDITIONS trigger CALL_SEQUENCE, breaking the equality EQUALITY, causing scoped impact: SCOPE_IMPACT against PARTY? Proof idea: go test PARAMETERS asserting SOUNDNESS, BATCH_EQUALS_MEMBERS, CELL_BINDING, CANONICAL_BYTES, SPEC_CONFORMANCE, or RECOVERY_TRUTH.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a KZG-soundness / spec-conformance exploit-validation prompt for go-eth-kzg.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: an ordinary Ethereum user who authors the blob, commitment, proof, cell, index or evaluation-point bytes that reach a public `Context` method through the normal protocol path. They may call any public method with any arguments.
- Reject anything requiring a dishonest trusted setup, a client developer wiring the library wrongly, an operator, a malicious peer/node/RPC, a compromised dependency or machine, or social engineering.
- OUT OF SCOPE, reject on sight: tests, benchmarks, tests/ fixtures, srs_insecure.go, trusted_setup.json, go.mod, CI config, audits/, readme; denial of service, panics as DoS, timeouts, unbounded loops, allocation, cache growth and memory hygiene; defects inside gnark-crypto with no path through this repo; side channels and timing; dishonest setup; best-practice notes; publicly known issues; theoretical findings.
- The impact must be one of: Critical - a proof, blob or cell batch accepted that is not a true opening, or non-canonical / out-of-subgroup bytes deserialised and then passing verification; High - this library and the consensus spec / c-kzg disagreeing on accept or on emitted commitment, proof, cell or recovered data for the same input, a valid proof rejected, or pooled state from one call changing another's result.
- Focus on real impact: something accepted that is not a true opening, or an output that differs from the spec for the same input.

## Validate
- Write the equality the question claims is broken between two named values BEFORE tracing any code.
- Trace the exact reachable path from the attacker's bytes and record every read and write of the deserialised commitment / proof point, `InputPoint`, `ClaimedValue`, the challenge, `rPowers` / `randomNumbers`, `rowIndices` / `cellIndices`, `cosetEvals`, pooled `polynomial` and `elementSlice` buffers, and the `PairingCheck` operands.
- Evaluate both sides of the equality before and after. If they still match, output no vulnerability.
- Check whether `SetBytes` / `SetBytesCanonical` subgroup and canonicity checks, `FindRootIndex`, the `batchSize` and length checks, `ErrInvalidRowIndex` / `ErrInvalidCellID`, `isAscending`, `NumBlocksNeededToReconstruct`, the `claimedValue` copy before `putPolynomial`, or the pairing equation itself already prevent the divergence.
- State what the attacker gains per call and whether it is repeatable.
- Require exact file/function support and a reproducible `go test` using the embedded trusted setup or tests/ vectors.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The broken equality, the code path, root cause, the attacker's exact bytes, exploit flow, and why existing guards fail]

### Impact Explanation
[What is accepted, rejected, mis-emitted or mis-recovered, which clients or chain state, repeatability, matching severity category]

### Likelihood Explanation
[Preconditions, batch shape and state required, attacker cost, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[go test plan with the exact assertions on both sides of the equality]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for go-eth-kzg claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- A claim is only valid if the report states the broken equality between two named values and shows both sides concretely. Reject prose-only claims.
- Reject anything requiring a dishonest trusted setup, a client developer wiring the library wrongly, an operator, a malicious peer/node/RPC, a compromised dependency or machine, or social engineering.
- OUT OF SCOPE, reject on sight: tests, benchmarks, tests/ fixtures, srs_insecure.go, trusted_setup.json, go.mod, CI config, audits/, readme; denial of service, panics as DoS, timeouts, unbounded loops, allocation, cache growth and memory hygiene; defects inside gnark-crypto with no path through this repo; side channels and timing; dishonest setup; centralization risk; best-practice notes; feature requests; publicly known issues; theoretical findings.
- The impact must be one of: Critical - a proof, blob or cell batch accepted that is not a true opening, or non-canonical / out-of-subgroup bytes deserialised and then passing verification; High - this library and the consensus spec / c-kzg disagreeing on accept or on emitted commitment, proof, cell or recovered data for the same input, a valid proof rejected, or pooled state from one call changing another's result.
- Reject claims where the only effect is on the attacker's own blob or proof being rejected.
- Reject if the bug was already fixed, publicly disclosed, or covered by a known-issues list.
- A valid report must be triggerable by an unprivileged party against the current code through the public `Context` surface.
- A PoC is mandatory. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function/method/constant, and line references.
2. The equality written explicitly, with both sides shown before and after.
3. Clear root cause: which pairing-operand drift, batch-folding gap, deserialisation gap, transcript mismatch, pooled-buffer aliasing, or recovery gap causes it.
4. Reachable exploit path: preconditions -> attacker bytes -> Context method, internal package and gnark call sequence -> observed divergence.
5. `SetBytes` / `SetBytesCanonical` checks, `FindRootIndex`, batch length checks, `ErrInvalidRowIndex` / `ErrInvalidCellID`, `isAscending`, `NumBlocksNeededToReconstruct`, the `claimedValue` copy and the pairing equation reviewed and shown insufficient.
6. Impact stated concretely: what is accepted or mis-emitted, which clients, and whether it is repeatable.
7. Reproducible proof: `go test` using the embedded trusted setup or tests/ vectors, with the asserted values.

## Silent Triage Questions
Before output, internally answer:
- What exactly is the equality, and does it actually fail?
- Can an ordinary user's authored bytes trigger it with no trusted role and no setup compromise?
- Is the flaw in this repo's code, not in gnark-crypto or the consensus spec itself?
- What is accepted, rejected, mis-emitted or mis-recovered, which clients, and can it be repeated?
- Would an Ethereum Foundation bug bounty triager accept the exploit path for the go-eth-kzg dependency?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the broken equality and impact]

## Finding Description
[Exact code path, the equality, root cause, exploit flow, and why existing guards fail]

## Impact Explanation
[What is accepted, rejected, mis-emitted or mis-recovered, affected clients, repeatability, severity category]

## Likelihood Explanation
[Attacker capability, preconditions, state required, cost, feasibility]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or go test plan with concrete assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for go-eth-kzg.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope repo context only (root package `*.go` and `internal/**/*.go`, excluding every `*_test.go`, tests/ fixtures, srs_insecure.go and trusted_setup.json). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged analogs that break an equality: a proof or batch accepted that is not a true opening, a cell accepted that is not the committed polynomial on its coset, bytes deserialised that are not a canonical subgroup element or scalar, a challenge or output that differs from the consensus spec / c-kzg for the same input, recovered cells that differ from the supplied ones, or a result that depends on pooled state from another call.
- OUT OF SCOPE, reject on sight: tests, benchmarks, fixtures, config, readme; denial of service, panics as DoS, timeouts, unbounded loops, allocation, cache growth and memory hygiene; defects inside gnark-crypto with no path here; anything requiring a dishonest trusted setup, a client developer, an operator or a compromised machine; malicious peer/node/RPC assumptions; side channels and timing; best-practice notes; theoretical findings.
- The impact must be one of: Critical - a proof, blob or cell batch accepted that is not a true opening, or non-canonical / out-of-subgroup bytes deserialised and then passing verification; High - this library and the consensus spec / c-kzg disagreeing on accept or on emitted commitment, proof, cell or recovered data for the same input, a valid proof rejected, or pooled state from one call changing another's result.
- Reject analogs where the only effect is on the attacker's own blob or proof.

## Validate
- Map the bug class to the strongest reachable path in this repo and state the equality it would break.
- Evaluate both sides before and after the attacker's bytes.
- Prove root cause with exact file/function support.
- Accept only concrete forged acceptance, spec divergence, wrong recovery, valid-proof rejection or cross-call state leakage.

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
