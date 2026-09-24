### Title
Unbounded quoted-number string passed to `strtod()` in upb JSON decoder enables CPU-exhaustion DoS during ProtoJSON parsing - (File: `upb/json/decode.c`)

### Summary
The Django advisory's failed invariant is: an attacker-controlled string is handed to an expensive validation/parsing routine (`clean_ipv6_address`/`is_valid_ipv6_address`) with no upper bound on its length, so a single request can trigger disproportionate CPU cost (CWE-770). The Protobuf analog is the upb ProtoJSON decoder's handling of quoted numeric strings for `double`/`float` fields: `jsondec_double()` reads an attacker-controlled JSON string value and passes it directly to libc `strtod()` with no length cap, while the exact same bug class (unbounded numeric string reaching an expensive parser) was already identified and explicitly fixed in the Java implementation of `JsonFormat`, but the fix was never propagated to the upb-based backend.

### Finding Description
In `upb/json/decode.c`, `jsondec_double()` parses a quoted numeric value for a `double`/`float` field as follows: [1](#0-0) 

The string `str` originates directly from the untrusted JSON payload via `jsondec_string(d)`, and its length is never checked before being handed to `strtod()`. Byte-for-byte identical code exists in the generated/vendored copies used by the PHP and Ruby extensions: [2](#0-1) [3](#0-2) 

Contrast this with the Java `com.google.protobuf.util.JsonFormat` implementation, which explicitly recognizes and mitigates this exact bug class for its numeric-string path (`BigDecimal` there, `strtod` here, both are known to exhibit pathological cost on adversarial long digit strings): [4](#0-3) 

The comment even documents the rationale — "BigDecimal(String) has O(N^2) time complexity for N-digit strings ... allowing a DoS with a single long numeric JSON value" — and the fix rejects any numeric string longer than 1000 characters before expensive parsing: [5](#0-4) 

This same protective invariant ("numeric strings passed to expensive parsers must have an upper bound, since valid protobuf numeric values never exceed ~350 characters") is absent from the upb decoder's `jsondec_double()` path. A JSON `string`-typed numeric field value (e.g. `{"optionalDouble": "999...999e1"}`) of attacker-chosen length is passed unbounded straight into glibc's `strtod()`, whose correctly-rounded conversion algorithm can take asymptotically worse-than-linear time on pathological long digit sequences (the same general class of float-parsing complexity issue that other ecosystems, including Python's own CPython `float()`/`int()` str conversion guards, have had to bound). The consuming application is any service that accepts ProtoJSON from an untrusted network client and parses it via `google.protobuf.util.JsonFormat` (C++/upb-backed builds), the PHP `google.protobuf` extension, or the Ruby `google-protobuf` gem, all of which route through this same `jsondec_double` code path — this satisfies the "ordinary client sending bounded ProtoJSON through a supported public parse API" exposure assumption, since only a single field's string value (not overall message size) needs to be adversarial.

### Impact Explanation
A single crafted `double`/`float` field encoded as a long numeric JSON string can consume disproportionate CPU time per parse call relative to its (bounded) payload size, degrading service availability for callers that expose ProtoJSON parsing to untrusted input (proxies, gateways, RPC front-ends using C++/upb, PHP, or Ruby protobuf bindings). This maps to CWE-770 (Allocation of Resources Without Limits or Throttling), matching the original Django advisory's classification and CVSS vector (availability-only impact, network-reachable, no privileges required).

### Likelihood Explanation
Likelihood is moderate-to-high for any service that accepts attacker-controlled ProtoJSON and uses the upb-based JSON decoder (default in current C++ protobuf JSON util builds, and in PHP/Ruby native extensions). No authentication or special conditions are required beyond the ability to submit a JSON payload with a `double`/`float` field expressed as a quoted string — a normal, documented ProtoJSON feature (quoted numeric values are explicitly permitted and tested, e.g. `DoubleFieldQuotedValue`) so there is no schema or parser-mode restriction blocking exploitation: [6](#0-5) 

### Recommendation
Apply the same length-bound invariant that was added to the Java `JsonFormat` numeric parser to the upb JSON decoder's `jsondec_double()` (and any equivalent quoted-numeric paths for int32/int64 if they route through similarly expensive conversions): reject quoted numeric strings above a small fixed bound (e.g. ~100–350 characters, matching the Java fix's rationale that valid protobuf numeric literals never approach that length) before calling `strtod()`, in `upb/json/decode.c`, and regenerate/sync the vendored copies in `php/ext/google/protobuf/php-upb.c` and `ruby/ext/google/protobuf_c/ruby-upb.c`.

### Proof of Concept
Conceptual reproduction (not executed in this environment — see caveat below): construct a ProtoJSON document for a message with a `double` field, e.g.
```json
{"optionalDouble": "1<many thousands of digits>e300"}
```
and parse it via the upb-backed `google::protobuf::util::JsonStringToMessage` (C++), `Google\Protobuf\Internal\Message::mergeFromJsonString` (PHP), or `Google::Protobuf::MessageExts#decode_json` (Ruby). All three route the quoted numeric string unmodified into `jsondec_double()` → `strtod(str.data, &end)` with no length pre-check, per the code cited above. I could not execute this PoC in this read-only environment; a background agent with build tooling would be needed to measure actual CPU cost of `strtod` on such adversarial strings and confirm the magnitude of the slowdown before finalizing severity, since exploitability depends on the specific libc `strtod` implementation's asymptotic behavior on the target platform.

### Citations

**File:** upb/json/decode.c (L782-796)
```c
    case JD_STRING:
      str = jsondec_string(d);
      if (str.size == 0) {
        jsondec_checkempty(d, str, f);
        val.double_val = 0.0;
      } else if (jsondec_streql(str, "NaN")) {
        val.double_val = NAN;
      } else if (jsondec_streql(str, "Infinity")) {
        val.double_val = INFINITY;
      } else if (jsondec_streql(str, "-Infinity")) {
        val.double_val = -INFINITY;
      } else {
        char* end;
        val.double_val = strtod(str.data, &end);
        if (end != str.data + str.size) {
```

**File:** php/ext/google/protobuf/php-upb.c (L5713-5734)
```c
    case JD_STRING:
      str = jsondec_string(d);
      if (str.size == 0) {
        jsondec_checkempty(d, str, f);
        val.double_val = 0.0;
      } else if (jsondec_streql(str, "NaN")) {
        val.double_val = NAN;
      } else if (jsondec_streql(str, "Infinity")) {
        val.double_val = INFINITY;
      } else if (jsondec_streql(str, "-Infinity")) {
        val.double_val = -INFINITY;
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

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L4466-4487)
```c
    case JD_STRING:
      str = jsondec_string(d);
      if (str.size == 0) {
        jsondec_checkempty(d, str, f);
        val.double_val = 0.0;
      } else if (jsondec_streql(str, "NaN")) {
        val.double_val = NAN;
      } else if (jsondec_streql(str, "Infinity")) {
        val.double_val = INFINITY;
      } else if (jsondec_streql(str, "-Infinity")) {
        val.double_val = -INFINITY;
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

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2309-2321)
```java
    // Maximum length for numeric strings passed to BigDecimal constructor.
    // BigDecimal(String) has O(N^2) time complexity for N-digit strings on JDK < 18,
    // allowing a DoS with a single long numeric JSON value. Valid protobuf numeric
    // values never exceed ~350 characters, so 1000 is a generous upper bound.
    private static final int MAX_NUMERIC_STRING_LENGTH = 1000;

    private static BigDecimal parseBigDecimal(String value) throws InvalidProtocolBufferException {
      if (value.length() > MAX_NUMERIC_STRING_LENGTH) {
        throw new InvalidProtocolBufferException(
            "Numeric value is too long: " + value.length() + " characters");
      }
      return new BigDecimal(value);
    }
```

**File:** conformance/binary_json_conformance_suite.cc (L2970-2975)
```text
  // Values can be quoted.
  RunValidJsonTest("DoubleFieldQuotedValue", REQUIRED,
                   R"({"optionalDouble": "1"})", "optional_double: 1");
  RunValidJsonTest("DoubleFieldQuotedExponentialValue", REQUIRED,
                   R"({"optionalDouble": "2.22507e-308"})",
                   "optional_double: 2.22507e-308");
```
