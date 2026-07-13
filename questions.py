import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
# todo: the path from https:///github.com/dfinity/ICRC-1
SOURCE_REPO = "Chia-Network/chia_rs"
# todo: the name of the repository
REPO_NAME = "chia_rs"
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
    "crates/chia-bls/src/bls_cache.rs",
    "crates/chia-bls/src/derive_keys.rs",
    "crates/chia-bls/src/error.rs",
    "crates/chia-bls/src/gtelement.rs",
    "crates/chia-bls/src/lib.rs",
    "crates/chia-bls/src/parse_hex.rs",
    "crates/chia-bls/src/public_key.rs",
    "crates/chia-bls/src/secret_key.rs",
    "crates/chia-bls/src/signature.rs",
    "crates/chia-client/src/error.rs",
    "crates/chia-client/src/lib.rs",
    "crates/chia-client/src/peer.rs",
    "crates/chia-client/src/utils.rs",
    "crates/chia-consensus/src/additions_and_removals.rs",
    "crates/chia-consensus/src/allocator.rs",
    "crates/chia-consensus/src/build_compressed_block.rs",
    "crates/chia-consensus/src/build_interned_block.rs",
    "crates/chia-consensus/src/check_time_locks.rs",
    "crates/chia-consensus/src/coin_id.rs",
    "crates/chia-consensus/src/condition_sanitizers.rs",
    "crates/chia-consensus/src/conditions.rs",
    "crates/chia-consensus/src/consensus_constants.rs",
    "crates/chia-consensus/src/error.rs",
    "crates/chia-consensus/src/fast_forward.rs",
    "crates/chia-consensus/src/flags.rs",
    "crates/chia-consensus/src/generator_cost.rs",
    "crates/chia-consensus/src/get_puzzle_and_solution.rs",
    "crates/chia-consensus/src/lib.rs",
    "crates/chia-consensus/src/make_aggsig_final_message.rs",
    "crates/chia-consensus/src/merkle_set.rs",
    "crates/chia-consensus/src/merkle_tree.rs",
    "crates/chia-consensus/src/messages.rs",
    "crates/chia-consensus/src/opcodes.rs",
    "crates/chia-consensus/src/owned_conditions.rs",
    "crates/chia-consensus/src/puzzle_fingerprint.rs",
    "crates/chia-consensus/src/run_block_generator.rs",
    "crates/chia-consensus/src/sanitize_int.rs",
    "crates/chia-consensus/src/solution_generator.rs",
    "crates/chia-consensus/src/spend_visitor.rs",
    "crates/chia-consensus/src/spendbundle_conditions.rs",
    "crates/chia-consensus/src/spendbundle_validation.rs",
    "crates/chia-consensus/src/validation_error.rs",
    "crates/chia-datalayer/macro/src/lib.rs",
    "crates/chia-datalayer/src/lib.rs",
    "crates/chia-datalayer/src/merkle/blob.rs",
    "crates/chia-datalayer/src/merkle/deltas.rs",
    "crates/chia-datalayer/src/merkle/dot.rs",
    "crates/chia-datalayer/src/merkle/error.rs",
    "crates/chia-datalayer/src/merkle/format.rs",
    "crates/chia-datalayer/src/merkle/iterators.rs",
    "crates/chia-datalayer/src/merkle/mod.rs",
    "crates/chia-datalayer/src/merkle/proof_of_inclusion.rs",
    "crates/chia-datalayer/src/merkle/util.rs",
    "crates/chia-protocol/src/block_record.rs",
    "crates/chia-protocol/src/bytes.rs",
    "crates/chia-protocol/src/chia_protocol.rs",
    "crates/chia-protocol/src/classgroup.rs",
    "crates/chia-protocol/src/coin.rs",
    "crates/chia-protocol/src/coin_record.rs",
    "crates/chia-protocol/src/coin_spend.rs",
    "crates/chia-protocol/src/coin_state.rs",
    "crates/chia-protocol/src/end_of_sub_slot_bundle.rs",
    "crates/chia-protocol/src/fee_estimate.rs",
    "crates/chia-protocol/src/foliage.rs",
    "crates/chia-protocol/src/full_node_protocol.rs",
    "crates/chia-protocol/src/fullblock.rs",
    "crates/chia-protocol/src/header_block.rs",
    "crates/chia-protocol/src/lazy_node.rs",
    "crates/chia-protocol/src/lib.rs",
    "crates/chia-protocol/src/partial_proof.rs",
    "crates/chia-protocol/src/peer_info.rs",
    "crates/chia-protocol/src/pool_target.rs",
    "crates/chia-protocol/src/pos_quality.rs",
    "crates/chia-protocol/src/pot_iterations.rs",
    "crates/chia-protocol/src/program.rs",
    "crates/chia-protocol/src/proof_of_space.rs",
    "crates/chia-protocol/src/reward_chain_block.rs",
    "crates/chia-protocol/src/slots.rs",
    "crates/chia-protocol/src/spend_bundle.rs",
    "crates/chia-protocol/src/sub_epoch_summary.rs",
    "crates/chia-protocol/src/unfinished_block.rs",
    "crates/chia-protocol/src/unfinished_header_block.rs",
    "crates/chia-protocol/src/utils.rs",
    "crates/chia-protocol/src/vdf.rs",
    "crates/chia-protocol/src/wallet_protocol.rs",
    "crates/chia-protocol/src/weight_proof.rs",
    "crates/chia-puzzle-types/src/derive_synthetic.rs",
    "crates/chia-puzzle-types/src/lib.rs",
    "crates/chia-puzzle-types/src/memos.rs",
    "crates/chia-puzzle-types/src/proof.rs",
    "crates/chia-puzzle-types/src/puzzles.rs",
    "crates/chia-puzzle-types/src/puzzles/cat.rs",
    "crates/chia-puzzle-types/src/puzzles/did.rs",
    "crates/chia-puzzle-types/src/puzzles/nft.rs",
    "crates/chia-puzzle-types/src/puzzles/offer.rs",
    "crates/chia-puzzle-types/src/puzzles/singleton.rs",
    "crates/chia-puzzle-types/src/puzzles/standard.rs",
    "crates/chia-secp/src/lib.rs",
    "crates/chia-secp/src/secp256k1.rs",
    "crates/chia-secp/src/secp256k1/public_key.rs",
    "crates/chia-secp/src/secp256k1/secret_key.rs",
    "crates/chia-secp/src/secp256k1/signature.rs",
    "crates/chia-secp/src/secp256r1.rs",
    "crates/chia-secp/src/secp256r1/public_key.rs",
    "crates/chia-secp/src/secp256r1/secret_key.rs",
    "crates/chia-secp/src/secp256r1/signature.rs",
    "crates/chia-serde/src/lib.rs",
    "crates/chia-sha2/src/lib.rs",
    "crates/chia-ssl/src/ca.rs",
    "crates/chia-ssl/src/error.rs",
    "crates/chia-ssl/src/lib.rs",
    "crates/chia-tools/src/lib.rs",
    "crates/chia-tools/src/visit_spends.rs",
    "crates/chia-traits/src/chia_error.rs",
    "crates/chia-traits/src/from_json_dict.rs",
    "crates/chia-traits/src/int.rs",
    "crates/chia-traits/src/lib.rs",
    "crates/chia-traits/src/streamable.rs",
    "crates/chia-traits/src/to_json_dict.rs",
    "crates/chia_py_streamable_macro/src/lib.rs",
    "crates/chia_streamable_macro/src/lib.rs",
    "crates/clvm-derive/src/apply_constants.rs",
    "crates/clvm-derive/src/from_clvm.rs",
    "crates/clvm-derive/src/helpers.rs",
    "crates/clvm-derive/src/lib.rs",
    "crates/clvm-derive/src/parser.rs",
    "crates/clvm-derive/src/parser/attributes.rs",
    "crates/clvm-derive/src/parser/enum_info.rs",
    "crates/clvm-derive/src/parser/field_info.rs",
    "crates/clvm-derive/src/parser/struct_info.rs",
    "crates/clvm-derive/src/parser/variant_info.rs",
    "crates/clvm-derive/src/to_clvm.rs",
    "crates/clvm-traits/src/clvm_decoder.rs",
    "crates/clvm-traits/src/clvm_encoder.rs",
    "crates/clvm-traits/src/error.rs",
    "crates/clvm-traits/src/from_clvm.rs",
    "crates/clvm-traits/src/int_encoding.rs",
    "crates/clvm-traits/src/lib.rs",
    "crates/clvm-traits/src/macros.rs",
    "crates/clvm-traits/src/match_byte.rs",
    "crates/clvm-traits/src/to_clvm.rs",
    "crates/clvm-traits/src/wrappers.rs",
    "crates/clvm-utils/src/curried_program.rs",
    "crates/clvm-utils/src/curry_tree_hash.rs",
    "crates/clvm-utils/src/hash_encoder.rs",
    "crates/clvm-utils/src/lib.rs",
    "crates/clvm-utils/src/tree_hash.rs",
    "src/lib.rs",
    "wasm/src/lib.rs",
    "wheel/python/chia_rs/__init__.py",
    "wheel/python/chia_rs/chia_rs.pyi",
    "wheel/python/chia_rs/datalayer.pyi",
    "wheel/python/chia_rs/sized_byte_class.py",
    "wheel/python/chia_rs/sized_bytes.py",
    "wheel/python/chia_rs/sized_ints.py",
    "wheel/python/chia_rs/spend.py",
    "wheel/python/chia_rs/struct_stream.py",
    "wheel/src/api.rs",
    "wheel/src/error.rs",
    "wheel/src/lib.rs",
    "wheel/src/run_generator.rs",
    "wheel/src/run_program.rs",
]
target_scopes = [
    "Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees",
    "Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption",
    "High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay",
    "High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data",
    "High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit + fuzzing questions for one chia_rs production target.

    ```
    target_file format:
    "'File Name: crates/chia-consensus/src/spendbundle_validation.rs -> Scope: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees'"
    ```
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact chia_rs target:

    {target_file}

    Project context:
    chia_rs is the Rust implementation of Chia consensus/protocol primitives with Python and wasm bindings. Sensitive surfaces include CLVM program execution, condition parsing/sanitization, spend bundle validation, coin ids, additions/removals, block generators, fast-forward/compression, BLS and secp signatures, streamable serialization, protocol structs, puzzle helpers, DataLayer Merkle logic, and binding conversions.

    Core invariants:
    * Consensus validation must deterministically accept only valid spends, blocks, proofs, conditions, signatures, timelocks, fees, and rewards.
    * Coin ids, tree hashes, Merkle roots, CLVM costs, serialized bytes, and signed messages must be canonical and identical across Rust, Python, wasm, and node callers.
    * Untrusted programs, spends, proofs, network objects, or binding inputs must not cause unauthorized spend acceptance, double-spend, inflation, state corruption, or validator disagreement.

    Rules:
    * Treat `File Name:` as the exact file/module and `Scope:` as the ONLY accepted impact.
    * Assume full repo context is accessible. Do not ask for code or say files are missing.
    * Attacker is unprivileged: wallet/user submitting spends, CLVM, blocks/proofs, serialized protocol data, DataLayer blobs/proofs, or Python/wasm API inputs.
    * Do not rely on malicious validators, governance, leaked keys, compromised nodes, dependency compromise, social engineering, local config mistakes, or network-level DoS only.
    * Ignore tests, mocks, docs, fuzz targets, benches, generated fixtures, scripts, CLI-only tooling, and low/medium/informational issues.
    * Generate 20 to 30 high-signal questions; at least 70% must be multi-step flow, invariant, fuzz, differential, accounting, replay, or cross-module questions.
    * Every question must be testable by PoC, unit test, fuzz test, invariant test, differential test, or local integration test.
    * Avoid generic checklist questions and repeated root causes.

    High-value attack surfaces:
    * Spend path: serialized SpendBundle/CoinSpend -> CLVM run -> conditions -> sanitizers -> aggsig/timelock/cost checks -> additions/removals.
    * Encoding/hash path: streamable bytes, sized ints/bytes, CLVM atom encoding, coin ids, tree hashes, Merkle set/tree roots, Python/wasm conversions.
    * Crypto/puzzle path: BLS aggregate verification/cache, secp verification, synthetic keys, CAT/NFT/DID/offer/singleton puzzle helpers.
    * Block/DataLayer path: block records, proof-of-space/VDF/weight proof structs, generator compression/fast-forward, DataLayer blob/delta/proof iteration.

    Allowed impacts only:
    * Critical coin theft, mint/burn bypass, double-spend, or reward/fee mis-accounting.
    * Critical deterministic consensus failure, chain halt, or committed state corruption from valid unprivileged input.
    * High unauthorized spend/replay via signature, puzzle, condition, timelock, or coin-id validation bypass.
    * High non-canonical parse/serialization/hash mismatch across consensus-critical boundaries.
    * High forged DataLayer proof/root/blob state acceptance.

    Each question must include:
    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: fuzz/state-test PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused chia_rs exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

Main focus is production chia_rs files from `scope_files`, especially consensus, protocol structs, CLVM traits/utils, puzzle types, BLS/secp crypto, streamable/serde traits, DataLayer Merkle logic, and Python/wasm bindings. Issues outside those files are out of scope unless required as direct supporting context.

## Scope Rules
- Audit only production chia_rs code.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, fuzz targets, benches, generated fixtures, scripts, CLI-only tooling, configs, and package metadata as audited targets.

## Objective
Decide whether the question leads to a real, reachable chia_rs vulnerability. The attacker must be unprivileged and enter through spend/block/proof/CLVM/serialized protocol/DataLayer/binding input. The impact must match one allowed Critical/High chia_rs impact below. Prefer #NoVulnerability unless the path is concrete, local-testable, and bounty-grade.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees.
- Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption.
- High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay.
- High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data.
- High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production chia_rs files/functions.
3. Check relevant guards: CLVM cost, condition sanitization, aggsig, timelock, coin id, tree hash, streamable canonicality, Merkle root/proof, or binding conversion.
4. Decide whether the invariant can break under intended deployment.
5. Prove root cause with file/function/line references.
6. Confirm realistic likelihood and exact scoped impact.
7. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires malicious validators, governance, privileged roles, leaked keys, compromised nodes, dependency compromise, chain reorgs, phishing, victim mistakes, or network-level DoS only.
- Only affects tests, docs, configs, scripts, mocks, fuzz targets, benches, generated fixtures, package metadata, or CLI-only tooling.
- Impact is only logging, local misconfiguration, non-security correctness, harmless reject/revert, performance, API ergonomics, or theoretical risk.
- No concrete Critical/High scoped impact or no realistic exploit path.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for chia_rs.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

Main focus is production chia_rs files from `scope_files`, especially consensus validation, protocol serialization, CLVM parsing/execution helpers, crypto verification, puzzle helpers, DataLayer Merkle code, and Python/wasm bindings. Issues outside those files are out of scope unless required as direct supporting context.

## Access Rules (Strict)
- Treat production chia_rs files in the provided scope as accessible context.
- Do not claim missing/inaccessible files or ask for repository contents.
- Do not scan tests, docs, generated fixtures, fuzz targets, benches, scripts, CLI-only tooling, configs, package metadata, or local assets as audited targets.

## Objective
Use the external report's vulnerability class only as a hint. Report an analog only if chia_rs has its own reachable root cause triggered by unprivileged spend/block/proof/CLVM/serialized protocol/DataLayer/binding input and the impact matches one allowed chia_rs impact below.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees.
- Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption.
- High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay.
- High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data.
- High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.

## Method
1. Classify vuln type: validation bypass, parser/canonicalization bug, hash/root mismatch, crypto misuse, CLVM cost/condition flaw, Merkle proof flaw, binding disagreement, or consensus nondeterminism.
2. Map to exact chia_rs components and production files.
3. Prove root cause with file/function/module/line references.
4. Confirm concrete scoped impact, likelihood, and attacker-controlled entry path.
5. Reject if the impact does not match one allowed Critical/High impact above.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires malicious validators, governance, privileged roles, leaked keys, compromised nodes, dependency compromise, phishing, chain reorgs, or network-level DoS only.
- External dependency behavior is the only cause.
- Test/docs/config/build/generated/fuzz/bench/tooling-only issue.
- Impact is only local misconfiguration, observability, harmless reject/revert, performance, or theoretical-only.

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
    Generate a strict chia_rs bounty-style validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

Main focus is production chia_rs files from `scope_files`: consensus, protocol, CLVM traits/utils, puzzle types, BLS/secp crypto, streamable/serde traits, DataLayer Merkle code, and Rust/Python/wasm binding boundaries. Supporting context is allowed, but audited targets must be production files in scope.

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and repository scope context when relevant.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-validator-only, governance-only, privileged-role-only, leaked-key, host-compromise, dependency-compromise, best-practice, docs/style, config/test-only, generated/fuzz/bench-only, tooling-only, performance-only, network-level-DoS-only, and purely theoretical issues.
- A valid report must be triggerable by an unprivileged user through spend/block/proof/CLVM/serialized protocol/DataLayer/Python/wasm inputs, unless it proves privilege escalation from an unprivileged path.
- The final impact must match an allowed Critical/High chia_rs impact, not just a generic code bug.
- Prefer #NoVulnerability over speculative reports.

## In-Scope Protocol Areas
- Consensus execution: block generators, CLVM costs, conditions, sanitizers, spend bundle validation, timelocks, additions/removals, coin ids, rewards, fees, and fast-forward/compression.
- Serialization and hashing: streamable bytes, sized ints/bytes, CLVM atom encoding, tree hashes, Merkle sets/trees, protocol structs, and cross-language canonicality.
- Crypto and puzzles: BLS aggregate verification/cache, secp signatures, synthetic keys, CAT/NFT/DID/offer/singleton/standard puzzle helpers, and signed message construction.
- DataLayer and bindings: Merkle blobs/deltas/proofs/iterators plus Rust/Python/wasm APIs when parsing or returning consensus-critical values.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees.
- Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption.
- High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay.
- High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data.
- High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.

Informational, Low, Medium, non-security correctness, logging-only, harmless reject/revert, local misconfiguration, API ergonomics, and non-demonstrably-exploitable reports are invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken consensus/security/accounting/authentication/canonicality assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete impact that exactly matches one allowed Critical/High impact above, with realistic likelihood.
6. Reproducible safe proof path: runnable PoC, deterministic unit/integration test, invariant/fuzz test, differential test, or exact local manual steps.

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete allowed chia_rs impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/differential test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
