## Title
Missing null-terminator on quoted JSON number causes out-of-bounds `strtod()` read in upb ProtoJSON decoder — (File: `upb/json/decode.c`)

## Summary
Protobuf's ProtoJSON allows numeric `double`/`float` fields to be encoded as JSON *strings* (e.g. `{"d":"1.5"}`), which is valid per the JSON canonical mapping. The upb decoder's `jsondec_double()` handles this quoted-number case by calling libc `strtod()` directly on the raw `upb_StringView` pointer taken from inside the attacker-supplied JSON buffer, without first copying it into a null-terminated scratch buffer. This is the same failure class as the reported YAML::Syck CVE-2025-11683: a scanner that assumes/consumes an implicit NUL terminator that the underlying buffer does not actually guarantee, leading to an out-of-bounds read.

## Finding Description
`jsondec_number()` (the JSON-*number-token* code path) is careful about this exact hazard — it explicitly copies the scanned digits into a fixed 64-byte, NUL-terminated scratch buffer before calling `strtod()`, with a comment noting the reason: [1](#0-0) 

However, the sibling code path for quoted numeric strings (`jsondec_double()`, used when a JSON *string* like `"1.5"`, `"NaN"`-adjacent values, etc. is supplied for a `double`/`float` field) skips that safeguard entirely and calls `strtod()` straight on `str.data`, a pointer that aliases the original input buffer returned by `jsondec_string()`: [2](#0-1) 

`str` here is a `upb_StringView { const char* data; size_t size; }` produced by `jsondec_string()`, i.e. a `(pointer, length)` pair into the *interior* of the caller-provided JSON buffer — it carries no guarantee of NUL-termination at `str.data + str.size`. `strtod()` is a C-string function: it has no length parameter and will keep scanning bytes at `str.data` until it hits a character it can't consume as part of a floating-point literal (a job normally done by a terminating `\0`). If the quoted number happens to sit at (or very near) the very end of the caller's buffer — e.g. the JSON document is exactly `{"d":"1"}` with no trailing bytes/padding allocated after it — `strtod()` will read past `str.data + str.size` into memory beyond the allocation, exactly mirroring the YAML::Syck `token.c` bug: a value is treated as a bounded token by the tokenizer/parser but handed to a C-string API without an actual terminator, causing the parser to walk off the end of the buffer.

The upstream fix applied in `jsondec_number()` (copy-to-null-terminated-scratch-buffer) is the correct mitigation and demonstrates the developers were aware of the hazard for one code path but not the other; `jsondec_double()`'s quoted-string branch is the analog that was missed.

This exact duplicated logic (including the same unguarded `strtod(str.data, &end)` call) is present in the generated/vendored copies of the upb runtime used by other language bindings: [3](#0-2) [4](#0-3) 

## Impact Explanation
An ordinary client sending a bounded ProtoJSON payload to any public parse API backed by upb (Ruby, PHP, and the C upb JSON decoder itself, reached via `upb_JsonDecode`) can trigger a read past the end of the input buffer when a `double`/`float` field is supplied as a quoted numeric string positioned at the tail of the buffer. This is an out-of-bounds read (information disclosure / potential crash under ASan or on a page boundary), matching the CVSS 3.1 vector class of the original report (`C:H/I:N/A:N`, no memory corruption, no code execution) — a read-only Medium-severity information-disclosure-class bug, not RCE. No privileged access or malicious schema is required; only an attacker-controlled bounded ProtoJSON body containing e.g. `{"value":"1"}` for a double/float field.

## Likelihood Explanation
The bug is reachable through the standard public ProtoJSON parsing entry point (`upb_JsonDecode`) with a fully valid, trusted `.proto` schema containing any `double`/`float` field — no adversarial schema or plugin needed. Whether it is *practically* exploitable (i.e., whether the byte immediately following the caller's buffer is actually inaccessible/unmapped) depends on how the embedding application allocates/passes the JSON buffer to `upb_JsonDecode` (many callers pass a `std::string`/heap buffer that likely has slack bytes after it, or a NUL-terminated C string as is common in these bindings, which would mask the bug in most real-world call patterns). This buffer-allocation dependency is the main uncertainty and could not be fully confirmed from the index alone (I was unable to trace every call site's exact buffer lifetime/allocation guarantees for `upb_JsonDecode` across Ruby/PHP/C++ bindings within the available tool budget).

## Recommendation
Apply the same defensive copy used in `jsondec_number()` to the quoted-string branch of `jsondec_double()`: copy the `upb_StringView` bytes into a bounded, NUL-terminated scratch buffer (or use a length-bounded parser such as `absl::SimpleAtod`/`from_chars`, as already used in the C++ JSON parser at `src/google/protobuf/json/internal/parser.cc`) before calling `strtod()`, and reject (rather than silently trust) any parse that consumes more/less than the view's length. Apply the same fix to the duplicated logic in `ruby-upb.c` and `php-upb.c`.

## Proof of Concept
Not run (no execution environment available in this session). Conceptually:
1. Build a minimal `.proto` with a `double` field, e.g. `message M { double d = 1; }`.
2. Allocate a heap buffer containing exactly `{"d":"1"}` (no extra padding), sized to exactly the JSON length, e.g. via `malloc(strlen(json))` + `memcpy` (or under ASan/Valgrind for a redzone to trigger the OOB detection).
3. Call `upb_JsonDecode(buf, len, msg, msg_def, symtab, 0, arena, &status)`.
4. Under ASan, `strtod()` scanning past `buf+len` (the `"1"` inside quotes, at the very tail) would be flagged as heap-buffer-overflow read, analogous to the YAML::Syck OOB read.

I was not able to execute this PoC or confirm the exact allocation behavior of every upb JSON entry point in this checkout; this should be verified with an ASan build before filing/fixing.

### Citations

**File:** upb/json/decode.c (L328-341)
```c
parse:
  /* Having verified the syntax of a JSON number, use strtod() to parse
   * (strtod() accepts a superset of JSON syntax). */
  errno = 0;
  {
    // Copy the number into a null-terminated scratch buffer since strtod
    // expects a null-terminated string.
    char nullz[64];
    ptrdiff_t len = d->ptr - start;
    if (len > (ptrdiff_t)(sizeof(nullz) - 1)) {
      jsondec_err(d, "excessively long number");
    }
    memcpy(nullz, start, len);
    nullz[len] = '\0';
```

**File:** upb/json/decode.c (L793-796)
```c
      } else {
        char* end;
        val.double_val = strtod(str.data, &end);
        if (end != str.data + str.size) {
```

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L4477-4487)
```c
      } else {
        char* end;
        val.double_val = strtod(str.data, &end);
        if (end != str.data + str.size) {
          d->result = kUpb_JsonDecodeResult_Error;
          upb_Status_SetErrorFormat(
              d->status,
              "Non-number characters in quoted number (field: %s). "
              "This will be an error in a future version.",
              upb_FieldDef_FullName(f));
        }
```

**File:** php/ext/google/protobuf/php-upb.c (L5724-5734)
```c
      } else {
        char* end;
        val.double_val = strtod(str.data, &end);
        if (end != str.data + str.size) {
          d->result = kUpb_JsonDecodeResult_Error;
          upb_Status_SetErrorFormat(
              d->status,
              "Non-number characters in quoted number (field: %s). "
              "This will be an error in a future version.",
              upb_FieldDef_FullName(f));
        }
```
